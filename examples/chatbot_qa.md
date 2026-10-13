# Chatbot QA with fishbowl

Prompt for Claude (or any MCP agent):

> Open https://your-store.example in fishbowl. For each question in `/workspace/questions.txt`:
> call `chat_ask`, then `judge_reply` with the label, the expected language and the facts in
> `/workspace/store_facts.md`. Write the results to `/workspace/results.csv` with columns
> question, label, reply, reply_type, language_match, actionable, cites_store_data, score.
> If the widget asks you to log in, call `handoff`.

Then `model_stats` tells you what the run cost.
