"""Delegates, through the engine: an owner adds an address after a card, lists the delegates and
removes one after a card; an address that holds data or delegates elsewhere is refused on the
request and again on confirmation; a delegate is refused all three; a delegate's welcome names the
owner's account, and an address that joins or leaves is greeted with its new place."""

from engine_kit import ACCOUNT, USER, Kit, edit

from common import texts
from common.access import DELEGATE, OWNER, User, resolve
from common.flows import card_text
from common.model import IntentResult

DANA = "dana@x.com"
OTHER = "other@x.com"


def _intent(name, address=None) -> IntentResult:
    return IntentResult(intent=name, address=address)


def _say(kit, intent, text="טקסט"):
    kit.model.queue(intent)
    return kit.say(text)


def _texts(result) -> list[str]:
    return [r.text for r in result.replies]


def _row(address, account=ACCOUNT, role=DELEGATE) -> dict:
    return {"email": address, "account": account, "role": role}


def _as_delegate(kit, address=DANA) -> Kit:
    return Kit(user=User(email=address, account=ACCOUNT, role=DELEGATE), store=kit.store)


def _waiting(kit, address):
    item = {"seq": "s1", "kind": "correction", "status": "awaiting", "step": "c", "version": 1}
    kit.store.put_pending(address, {**item, "data": {}})


def _add(kit, address="Dana@x.com"):
    return _say(kit, _intent("add_delegate", address), f"הוסף את {address} כמורשה")


def _confirm(kit):
    return _say(kit, IntentResult(intent="confirm"), "אישור")


def test_adding_shows_a_card_naming_the_address_and_writes_its_row_on_confirm(env, ddb):
    kit = Kit()
    kit.apartment()
    _waiting(kit, DANA)
    result = _add(kit)
    card = result.snapshot.card
    assert card["kind"] == "delegate_add"
    title, lines = texts.delegate_add_card(DANA)
    assert (card["title"], card["lines"]) == (title, lines)
    assert title == "הוספת מורשה" and DANA in lines[0]
    assert [o["label"] for o in card["options"]] == ["אישור"]
    assert kit.store.access_get(DANA) is None
    result = _confirm(kit)
    assert _texts(result) == [texts.delegate_added(DANA)]
    assert result.snapshot.card is None
    assert kit.store.access_get(DANA) == _row(DANA)
    assert resolve(kit.store, "Dana@x.com").account == ACCOUNT
    assert kit.store.pending(DANA) == []


def test_listing_names_the_delegates_or_says_there_are_none(env, ddb):
    kit = Kit()
    assert _texts(_say(kit, _intent("list_delegates"))) == [texts.no_delegates()]
    kit.store.access_put_if_absent("b@x.com", ACCOUNT, DELEGATE)
    kit.store.access_put_if_absent("a@x.com", ACCOUNT, DELEGATE)
    assert _texts(_say(kit, _intent("list_delegates"))) == [
        texts.delegates_list(["a@x.com", "b@x.com"])
    ]
    assert kit.store.pending(USER.email) == []


def test_removing_deletes_the_row_after_a_card_so_the_next_sign_in_opens_an_own_account(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, ACCOUNT, DELEGATE)
    _waiting(kit, DANA)
    result = _say(kit, _intent("remove_delegate", "Dana@x.com"))
    card = result.snapshot.card
    assert card["kind"] == "delegate_remove"
    assert card["lines"] == [texts.access_lost(DANA)]
    assert kit.store.access_get(DANA) == _row(DANA)
    result = _confirm(kit)
    assert _texts(result) == [texts.delegate_removed(DANA)]
    assert kit.store.access_get(DANA) is None
    assert kit.store.pending(DANA) == []
    assert resolve(kit.store, DANA) == User(email=DANA, account=DANA, role=OWNER)


def test_removing_an_address_that_is_no_delegate_says_so(env, ddb):
    kit = Kit()
    assert _texts(_say(kit, _intent("remove_delegate", DANA))) == [texts.unknown_delegate(DANA)]
    assert kit.store.pending(USER.email) == []


def test_rejecting_a_card_leaves_the_delegates_as_they_were(env, ddb):
    kit = Kit()
    _add(kit)
    result = _say(kit, IntentResult(intent="reject"), "בטל")
    assert _texts(result) == [texts.delegates_unchanged()]
    assert kit.store.access_get(DANA) is None
    assert kit.store.pending(USER.email) == []


def test_an_address_whose_account_holds_data_is_refused_and_keeps_its_row(env, ddb):
    kit = Kit()
    other = Kit(user=User(email=OTHER, account=OTHER, role=OWNER), store=kit.store)
    kit.store.access_put_if_absent(OTHER, OTHER, OWNER)
    other.apartment()
    assert _texts(_add(kit, OTHER)) == [texts.address_taken()]
    assert kit.store.pending(USER.email) == []
    assert kit.store.access_get(OTHER) == _row(OTHER, OTHER, OWNER)


def test_an_address_that_delegates_for_another_owner_is_refused_and_keeps_its_row(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, OTHER, DELEGATE)
    assert _texts(_add(kit)) == [texts.address_taken()]
    assert kit.store.pending(USER.email) == []
    assert kit.store.access_get(DANA) == _row(DANA, OTHER)


def test_an_address_that_signed_in_with_no_data_becomes_a_delegate(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, DANA, OWNER)
    _add(kit)
    _confirm(kit)
    assert kit.store.access_get(DANA) == _row(DANA)


def test_an_address_that_gained_data_while_the_card_waited_is_refused_on_confirmation(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, DANA, OWNER)
    _add(kit)
    Kit(user=User(email=DANA, account=DANA, role=OWNER), store=kit.store).apartment()
    result = _confirm(kit)
    assert _texts(result) == [texts.address_taken()]
    assert result.snapshot.card is None
    assert kit.store.access_get(DANA) == _row(DANA, DANA, OWNER)


def test_the_owners_own_address_and_a_current_delegate_are_refused(env, ddb):
    kit = Kit()
    assert _texts(_add(kit, ACCOUNT)) == [texts.own_address()]
    kit.store.access_put_if_absent(DANA, ACCOUNT, DELEGATE)
    assert _texts(_add(kit)) == [texts.delegate_exists(DANA)]
    assert kit.store.pending(USER.email) == []


def test_an_add_with_no_usable_address_asks_on_a_card_that_a_bare_address_answers(env, ddb):
    kit = Kit()
    for typed in (None, "dana"):
        card = _add(kit, typed).snapshot.card
        assert card["kind"] == "delegate_ask"
        assert (card["title"], card["lines"]) == texts.delegate_ask_card()
        assert card["options"] == []
        assert kit.form() == {"delegate_address": None}
    assert len(kit.store.pending(USER.email)) == 1
    _say(kit, IntentResult(intent="confirm"), "אישור")
    assert kit.store.access_get(DANA) is None
    result = _say(kit, edit(None, "Dana@x.com"), "Dana@x.com")
    assert result.snapshot.card["kind"] == "delegate_add"
    _confirm(kit)
    assert kit.store.access_get(DANA) == _row(DANA)


def test_a_remove_with_no_usable_address_asks_for_one(env, ddb):
    kit = Kit()
    result = _say(kit, _intent("remove_delegate"))
    assert _texts(result) == [texts.ask_delegate_address()]
    assert kit.store.pending(USER.email) == []


def test_a_mistyped_address_is_corrected_on_the_add_card(env, ddb):
    kit = Kit()
    _add(kit, "dana@x.con")
    result = _say(kit, edit("delegate_address", "Dana@x.com"), "dana@x.com")
    assert DANA in card_text(result.snapshot.card)
    assert kit.form() == {"delegate_address": DANA}
    _confirm(kit)
    assert kit.store.access_get("dana@x.con") is None
    assert kit.store.access_get(DANA) == _row(DANA)


def test_an_edit_to_a_refused_address_keeps_the_card_as_it_was(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(OTHER, ACCOUNT, DELEGATE)
    card = _add(kit).snapshot.card
    result = _say(kit, edit(None, OTHER), OTHER)
    assert _texts(result) == [texts.delegate_exists(OTHER), card_text(card)]
    assert kit.form() == {"delegate_address": DANA}


def test_a_delegate_is_refused_adding_listing_and_removing(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, ACCOUNT, DELEGATE)
    delegate = _as_delegate(kit)
    for intent in ("add_delegate", "list_delegates", "remove_delegate"):
        assert _texts(_say(delegate, _intent(intent, OTHER))) == [texts.owner_only()]
    assert kit.store.pending(DANA) == []
    assert kit.store.access_get(OTHER) is None


def test_only_an_owners_welcome_calls_the_account_new(env, ddb):
    kit = Kit()
    [message] = kit.engine.load(USER)[0]
    assert message["text"] == texts.welcome(OWNER, ACCOUNT)
    assert "חדש" in message["text"] and "מורשה" not in message["text"]
    [message] = _as_delegate(kit).engine.load(User(email=DANA, account=ACCOUNT, role=DELEGATE))[0]
    assert message["text"] == texts.welcome(DELEGATE, ACCOUNT)
    assert "חדש" not in message["text"]
    assert ACCOUNT in message["text"] and "מורשה" in message["text"]


def _last_message(kit, address, account, role) -> str:
    return kit.engine.load(User(email=address, account=account, role=role))[0][-1]["text"]


def test_an_added_address_that_signed_in_before_is_greeted_as_a_delegate(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, DANA, OWNER)
    signed_in = User(email=DANA, account=DANA, role=OWNER)
    assert kit.engine.load(signed_in)[0][-1]["text"] == texts.welcome(OWNER, DANA)
    _add(kit)
    _confirm(kit)
    assert _last_message(kit, DANA, ACCOUNT, DELEGATE) == texts.welcome(DELEGATE, ACCOUNT)


def test_a_removed_delegate_is_greeted_as_the_owner_of_its_own_account(env, ddb):
    kit = Kit()
    _add(kit)
    _confirm(kit)
    kit.engine.load(User(email=DANA, account=ACCOUNT, role=DELEGATE))
    _say(kit, _intent("remove_delegate", DANA))
    _confirm(kit)
    assert _last_message(kit, DANA, DANA, OWNER) == texts.welcome(OWNER, DANA)


def test_a_corrected_delegate_address_greets_both_addresses(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(DANA, ACCOUNT, DELEGATE)
    kit.engine.load(User(email=DANA, account=ACCOUNT, role=DELEGATE))
    kit.engine.load(User(email=OTHER, account=OTHER, role=OWNER))
    _say(kit, edit("address", OTHER, target=f"delegate|{DANA}"))
    _confirm(kit)
    assert kit.store.access_get(OTHER) == _row(OTHER)
    assert _last_message(kit, OTHER, ACCOUNT, DELEGATE) == texts.welcome(DELEGATE, ACCOUNT)
    assert _last_message(kit, DANA, DANA, OWNER) == texts.welcome(OWNER, DANA)
