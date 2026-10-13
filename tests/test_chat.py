"""chat_ask against a simulated support bot, plus judge_reply and reply extraction."""

from fishbowl import chat, llm

from .fake_model import use_fake


async def test_chat_ask_answered(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html?mode=answer")
    r = await chat.chat_ask(browser, "Where is my order?", timeout_s=15, stable_s=1.2)
    assert r["label"] == "answered"
    assert r["reply"] == "Your order #1042 shipped yesterday and arrives Friday. Track it at example.com/track."
    assert "Thinking" not in r["reply"] and "Powered by" not in r["reply"]


async def test_chat_ask_ignores_indonesian_placeholder(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html?mode=sedang")
    r = await chat.chat_ask(browser, "Di mana pesanan saya?", timeout_s=15, stable_s=1.2)
    assert r["label"] == "answered"
    assert r["reply"] == "Pesanan Anda sedang dikirim dan tiba besok."


async def test_chat_ask_survey_only(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html?mode=survey")
    r = await chat.chat_ask(browser, "Can I return this?", timeout_s=10, stable_s=1.2)
    assert r["label"] == "survey_only"


async def test_chat_ask_no_reply(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html?mode=silent")
    r = await chat.chat_ask(browser, "Hello?", timeout_s=3, stable_s=1)
    assert r["label"] == "no_reply" and r["timed_out"]


async def test_chat_ask_send_failed(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html?mode=nosend")
    r = await chat.chat_ask(browser, "Is anyone there?", timeout_s=3)
    assert r["label"] == "send_failed"


async def test_chat_ask_blocks_secrets(browser, page_server):
    await browser.navigate(f"{page_server}/chat.html")
    r = await chat.chat_ask(browser, "my card is 4242 4242 4242 4242")
    assert r["label"] == "blocked"


def test_extract_reply_strips_chrome_and_placeholders():
    before = "Support chat\nHi!\n➤\nPowered by DemoBot"
    after = "Support chat\nHi!\nWhere?\nThinking...\nIt shipped.\n➤\nPowered by DemoBot"
    assert chat.extract_reply(before, after, "Where?") == "It shipped."


def test_classify():
    assert chat.classify("", False, False) == "send_failed"
    assert chat.classify("", True, False) == "no_reply"
    assert chat.classify("", True, True) == "placeholder_stuck"
    assert chat.classify("Was this helpful? 👍 👎", True, False) == "survey_only"
    assert chat.classify("Your refund is on the way.\nWas this helpful?", True, False) == "answered"


async def test_judge_no_reply_skips_model():
    llm.set_override(None)  # would fail if called (no key, no ollama in CI)
    r = await chat.judge_reply("q", "", label="no_reply")
    assert r["reply_type"] == "no_reply" and r["source"] == "rule"
    r = await chat.judge_reply("q", "How satisfied are you?", label="survey_only")
    assert r["reply_type"] == "survey_only"


async def test_judge_uses_model():
    use_fake(llm)
    r = await chat.judge_reply("Where is order 1042?", "Order #1042 ships Friday.", label="answered")
    assert r["source"] == "model" and r["cites_store_data"] is True
    assert llm.stats()["by_purpose"]["judge"]["calls"] == 1
