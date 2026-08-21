Output demo/metusk.gif

Set Shell "bash"
Set Width 1200
Set Height 760
Set FontSize 16
Set TypingSpeed 25ms
Set PlaybackSpeed 1.25

Type "rm -rf demo/.tmp && mkdir -p demo/.tmp"
Enter
Type "uv run metusk serve --data-dir demo/.tmp/data --port 8765 >demo/.tmp/server.log 2>&1 & SERVER_PID=$!"
Enter
Sleep 2s
Type "uv run metusk langgraph-demo --url http://127.0.0.1:8765"
Enter
Sleep 2s
Type "TRAIL=$(find demo/.tmp/data/trails -name '*.jsonl' | head -1)"
Enter
Type "uv run metusk verify $TRAIL --public-key demo/.tmp/data/public_key.pem"
Enter
Sleep 2s
Type "cp $TRAIL demo/.tmp/tampered.jsonl && sed -i '2s/multiply/multiply-edited/' demo/.tmp/tampered.jsonl"
Enter
Type "uv run metusk verify demo/.tmp/tampered.jsonl --public-key demo/.tmp/data/public_key.pem"
Enter
Sleep 2s
Type "kill $SERVER_PID && wait $SERVER_PID 2>/dev/null || true"
Enter
Type "rm -rf demo/.tmp"
Enter
