import pytest

from fishbowl import guard, llm

# (action, target element, needs approval?)
LABELED = [
    ("click", 'button "Place order"', True),
    ("click", 'button "Pay now"', True),
    ("click", 'button "Buy now"', True),
    ("click", 'button "Confirm payment"', True),
    ("click", 'button "Send"', True),
    ("click", 'button "Send message"', True),
    ("click", 'button "Post comment"', True),
    ("click", 'button "Publish"', True),
    ("click", 'button "Delete account"', True),
    ("click", 'button "Remove item"', True),
    ("click", 'button "Submit application"', True),
    ("click", 'button "Transfer funds"', True),
    ("click", 'button "提交订单"', True),
    ("click", 'button "立即支付"', True),
    ("click", 'button "Bayar sekarang"', True),
    ("click", 'button "Kirim"', True),
    ("click", 'button "Search"', False),
    ("click", 'link "Next page"', False),
    ("click", 'button "Close"', False),
    ("click", 'button "Accept all cookies"', False),
    ("click", 'tab "Shipping"', False),
    ("click", 'link "Product details"', False),
    ("click", 'button "Show more"', False),
    ("click", 'checkbox "Remember me"', False),
    ("click", 'button "Filter"', False),
    ("click", 'link "Back to results"', False),
    ("click", 'button "Open menu"', False),
    ("click", 'combobox "Sort by"', False),
    ("type", 'textbox "Message"', False),
    ("click", 'button "Learn more"', False),
]

HOLDOUT = [
    ("click", 'button "Checkout"', True),
    ("click", 'button "Reply"', True),
    ("click", 'button "Unsubscribe"', True),
    ("click", 'button "删除"', True),
    ("click", 'button "Hapus"', True),
    ("click", 'link "Previous"', False),
    ("click", 'button "View cart"', False),
    ("click", 'button "Dismiss"', False),
    ("click", 'radio "Express shipping"', False),
    ("click", 'button "Cancel"', False),
]


def _score(rows):
    tp = sum(1 for a, t, y in rows if y and guard.heuristic(a, t).needs_approval)
    fp = sum(1 for a, t, y in rows if not y and guard.heuristic(a, t).needs_approval)
    pos = sum(1 for *_, y in rows if y)
    return tp / pos, fp / (len(rows) - pos)


@pytest.mark.parametrize("rows", [LABELED, HOLDOUT], ids=["labeled", "holdout"])
def test_heuristic_fallback_recall_and_false_blocks(rows):
    recall, false_block = _score(rows)
    assert recall == 1.0
    assert false_block == 0.0


@pytest.mark.parametrize("text", [
    "4111 1111 1111 1111", "4242-4242-4242-4242", "my card 5555555555554444 thanks",
    "sk-ant-api03-" + "a" * 40, "sk-proj-" + "b" * 40, "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "c" * 36, "xoxb-1234567890-abcdef", "-----BEGIN RSA PRIVATE KEY-----",
])
def test_secrets_are_detected(text):
    assert guard.contains_secret(text)


@pytest.mark.parametrize("text", [
    "order 1234567", "call me at +62 812 3456 7890", "1111111111111111", "the sky is blue",
    "4111 1111 1111 1112",  # fails Luhn
])
def test_non_secrets_pass(text):
    assert not guard.contains_secret(text)


async def test_assess_uses_model_and_respects_allow():
    async def model(purpose, system, user, schema):
        return {"needs_approval": True, "category": "send_message", "reason": "sends a chat"}

    llm.set_override(model)
    v = await guard.assess("click", 'button "Send"')
    assert v.needs_approval and v.source == "model"
    v = await guard.assess("click", 'button "Send"', allowed=["send_message"])
    assert not v.needs_approval


async def test_assess_fails_closed_without_model(monkeypatch):
    monkeypatch.setenv("FISHBOWL_LLM", "ollama")
    monkeypatch.setenv("FISHBOWL_LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    v = await guard.assess("click", 'button "Delete account"')
    assert v.needs_approval and v.source == "heuristic"


async def test_non_commit_actions_skip_the_model():
    llm.set_override(None)
    v = await guard.assess("scroll", "page")
    assert not v.needs_approval
