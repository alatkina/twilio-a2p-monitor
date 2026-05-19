# Twilio A2P Monitor

Use this command when the user asks for Twilio A2P / 10DLC status for a subaccount.

Expected usage:
- /twilio-a2p-monitor HiLine
- /twilio-a2p-monitor Gold-Eagle
- /twilio-a2p-monitor failed
- /twilio-a2p-monitor

Steps:
1. Get the user query after the command.
2. Run GitHub Actions workflow `Twilio Monitor`.
3. Pass the query as workflow input `account`.
4. Wait for workflow completion.
5. Download artifact `twilio-result`.
6. Read `result.md`.
7. Answer the user in plain language.

Commands to run:

```bash
gh workflow run "Twilio Monitor" -f account="$ARGUMENTS"

sleep 10

RUN_ID=$(gh run list --workflow="Twilio Monitor" --limit=1 --json databaseId --jq '.[0].databaseId')

gh run watch "$RUN_ID" --exit-status

rm -rf /tmp/twilio-result

mkdir -p /tmp/twilio-result

gh run download "$RUN_ID" -n twilio-result -D /tmp/twilio-result

cat /tmp/twilio-result/result.md
