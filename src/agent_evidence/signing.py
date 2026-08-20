"""ECDSA P-256 signing for independently recorded audit records."""

import base64
import hashlib
import os
from pathlib import Path

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from agent_evidence.models import AuditRecord


def _public_der(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def component_uri_from_public_key(public_key_pem: bytes) -> str:
    key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("public key must be an ECDSA P-256 key")
    return f"urn:agent-evidence:recorder:{hashlib.sha256(_public_der(key)).hexdigest()}"


def _signing_digest(record: AuditRecord) -> bytes:
    canonical = rfc8785.dumps(record.json_compatible(include_signature=False))
    return hashlib.sha256(canonical).digest()


class RecordSigner:
    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        if not isinstance(private_key.curve, ec.SECP256R1):
            raise ValueError("private key must use ECDSA P-256")
        self._private_key = private_key

    @classmethod
    def generate(cls) -> "RecordSigner":
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def load(cls, path: Path) -> "RecordSigner":
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("private key is missing or malformed") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("private key must be an ECDSA P-256 key")
        return cls(key)

    def save_private_key(self, path: Path) -> None:
        pem = self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        try:
            with path.open("xb") as stream:
                stream.write(pem)
        except FileExistsError:
            raise FileExistsError("private key already exists") from None
        if os.name == "posix":
            path.chmod(0o600)

    def public_key_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def component_uri(self) -> str:
        return component_uri_from_public_key(self.public_key_pem())

    def sign_record(self, record: AuditRecord) -> str:
        der = self._private_key.sign(
            _signing_digest(record),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
        r, s = utils.decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_record_signature(record: AuditRecord, public_key_pem: bytes) -> bool:
    try:
        if record.signature is None:
            return False
        raw = base64.urlsafe_b64decode(record.signature + "==")
        if len(raw) != 64:
            return False
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            return False
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        key.verify(
            utils.encode_dss_signature(r, s),
            _signing_digest(record),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
