from base64 import urlsafe_b64encode

from agent_evidence.canonical import record_hash
from agent_evidence.models import AuditRecord, Outcome
from agent_evidence.signing import RecordSigner, verify_record_signature


def signed_record(record_data, signer):
    record_data["recording_component"] = signer.component_uri()
    unsigned = AuditRecord.model_validate(record_data)
    return AuditRecord.model_validate(
        {**unsigned.json_compatible(), "signature": signer.sign_record(unsigned)}
    )


def test_generate_save_load_is_stable(tmp_path):
    path = tmp_path / "private.pem"
    signer = RecordSigner.generate()
    signer.save_private_key(path)
    loaded = RecordSigner.load(path)
    assert loaded.public_key_pem() == signer.public_key_pem()
    assert loaded.component_uri() == signer.component_uri()


def test_valid_modified_and_wrong_key_signatures(record_data):
    signer = RecordSigner.generate()
    record = signed_record(record_data, signer)
    assert verify_record_signature(record, signer.public_key_pem())
    modified = record.model_copy(update={"outcome": Outcome.FAILURE})
    assert not verify_record_signature(modified, signer.public_key_pem())
    assert not verify_record_signature(record, RecordSigner.generate().public_key_pem())


def test_signature_is_excluded_but_previous_signature_is_hashed(record_data):
    signer = RecordSigner.generate()
    record = signed_record(record_data, signer)
    replacement = record.model_copy(
        update={"signature": urlsafe_b64encode(b"x" * 64).rstrip(b"=").decode()}
    )
    resigned = replacement.model_copy(
        update={"signature": signer.sign_record(replacement)}
    )
    assert verify_record_signature(resigned, signer.public_key_pem())
    assert record_hash(record) != record_hash(replacement)


def test_malformed_signature_returns_false(record_data):
    signer = RecordSigner.generate()
    record = signed_record(record_data, signer)
    for malformed in ("%%%", urlsafe_b64encode(b"short").rstrip(b"=").decode()):
        invalid = record.model_copy(update={"signature": malformed})
        assert not verify_record_signature(invalid, signer.public_key_pem())
