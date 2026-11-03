"""SmartThings refusals: one TV or the whole account.

From 15 December 2026 Samsung deactivates the API keys of owners without a paid
SmartThings API plan. Every TV on the account is then refused with the same
Forbidden that used to mean "this TV re-registered under a new id", and the
old notice sent everyone to Reconfigure > SmartThings device for nothing.

The notice now names both causes for a single refused TV, and says it is the
account when every SmartThings TV of the install is refused at once.
"""

from types import SimpleNamespace

from custom_components.samsungtv_smart import token_notify as tn


class _Notifications:
    def __init__(self):
        self.shown = {}

    def async_create(self, hass, message, title=None, notification_id=None):
        self.shown[notification_id] = (title, message)

    def async_dismiss(self, hass, notification_id):
        self.shown.pop(notification_id, None)


def _hass(monkeypatch, language="en"):
    notes = _Notifications()
    monkeypatch.setattr(tn, "persistent_notification", notes)
    hass = SimpleNamespace(data={}, config=SimpleNamespace(language=language))
    return hass, notes


def _title(notes, entry_id):
    return notes.shown[tn._notification_id(entry_id, tn.METHOD_SMARTTHINGS)][0]


def _message(notes, entry_id):
    return notes.shown[tn._notification_id(entry_id, tn.METHOD_SMARTTHINGS)][1]


def test_a_single_refused_tv_gets_both_causes(monkeypatch):
    hass, notes = _hass(monkeypatch)
    tn.report_smartthings_access(hass, "a", "Salon", True)
    message = _message(notes, "a")
    assert "15 December 2026" in message
    assert "No SmartThings (local only)" in message
    assert "SmartThings device" in message
    assert _title(notes, "a") == tn._TITLES["en"][tn.METHOD_SMARTTHINGS]


def test_one_refused_among_working_tvs_is_that_tv(monkeypatch):
    hass, notes = _hass(monkeypatch)
    tn.report_smartthings_access(hass, "a", "Salon", False)
    tn.report_smartthings_access(hass, "b", "Chambre", True)
    assert _title(notes, "b") == tn._TITLES["en"][tn.METHOD_SMARTTHINGS]
    assert tn._notification_id("a", tn.METHOD_SMARTTHINGS) not in notes.shown


def test_every_tv_refused_points_to_the_account(monkeypatch):
    hass, notes = _hass(monkeypatch)
    tn.report_smartthings_access(hass, "a", "Salon", True)
    tn.report_smartthings_access(hass, "b", "Chambre", True)
    plan_title = tn._TITLES["en"][tn.METHOD_SMARTTHINGS_PLAN]
    assert _title(notes, "a") == plan_title
    assert _title(notes, "b") == plan_title
    assert "every TV on this Samsung account" in _message(notes, "a")
    assert "Salon" in _message(notes, "a")


def test_a_tv_answering_again_clears_its_notice_and_rewords_the_others(
    monkeypatch,
):
    hass, notes = _hass(monkeypatch)
    tn.report_smartthings_access(hass, "a", "Salon", True)
    tn.report_smartthings_access(hass, "b", "Chambre", True)
    tn.report_smartthings_access(hass, "a", "Salon", False)
    assert tn._notification_id("a", tn.METHOD_SMARTTHINGS) not in notes.shown
    assert _title(notes, "b") == tn._TITLES["en"][tn.METHOD_SMARTTHINGS]


def test_an_unloaded_tv_leaves_the_check(monkeypatch):
    hass, notes = _hass(monkeypatch)
    tn.report_smartthings_access(hass, "a", "Salon", False)
    tn.forget_smartthings_access(hass, "a")
    tn.report_smartthings_access(hass, "b", "Chambre", True)
    tn.report_smartthings_access(hass, "c", "Bureau", True)
    assert _title(notes, "b") == tn._TITLES["en"][tn.METHOD_SMARTTHINGS_PLAN]


def test_repeated_success_does_nothing(monkeypatch):
    hass, notes = _hass(monkeypatch)
    calls = []
    monkeypatch.setattr(tn, "clear_token_problem", lambda *a: calls.append(a))
    tn.report_smartthings_access(hass, "a", "Salon", False)
    tn.report_smartthings_access(hass, "a", "Salon", False)
    assert len(calls) == 1


def test_french(monkeypatch):
    hass, notes = _hass(monkeypatch, "fr")
    tn.report_smartthings_access(hass, "a", "Salon", True)
    tn.report_smartthings_access(hass, "b", "Chambre", True)
    assert "tous les téléviseurs de ce compte" in _message(notes, "a")
    assert "15 décembre 2026" in _message(notes, "a")
