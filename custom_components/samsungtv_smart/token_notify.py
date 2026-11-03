"""Persistent-notification helpers for Samsung TV token problems.

Two of the three authentication paths used by this integration have no native
Home Assistant surface for an invalid credential:

  * the local WebSocket token (PAT) on port 8002, and
  * the IP Control AccessToken (JSON-RPC) on port 1516.

When either token is rejected by the TV, the integration would otherwise loop —
re-requesting a local token re-arms the on-screen "Allow to connect?" prompt, and
IP Control calls keep failing silently. These helpers raise a single, stable,
self-clearing persistent notification per (entry, method) so the user knows
exactly which credential is bad and how to fix it.

SmartThings OAuth is deliberately NOT handled here: it already surfaces through
the Repairs issue registry (see media_player._raise_oauth_issue), which is the
native path and auto-clears on a successful refresh.

persistent_notification does not support translation keys, so the title/message
are localized here based on ``hass.config.language`` (French when it starts with
"fr", English otherwise).

All functions are Home Assistant callbacks and must run on the event loop. The
threaded WebSocket layer (samsungws) reaches them via run_callback_threadsafe
through media_player's loop-side callbacks, never directly.
"""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

# Token methods that use this maison notification (SmartThings excluded — it
# uses the native Repairs issue registry instead).
METHOD_LOCAL = "local"
METHOD_IP_CONTROL = "ip_control"
METHOD_SMARTTHINGS = "smartthings"
# Same notification as METHOD_SMARTTHINGS, worded for every SmartThings TV of
# the install being refused at once - the account, not one device.
METHOD_SMARTTHINGS_PLAN = "smartthings_plan"

_TITLES: dict[str, dict[str, str]] = {
    "en": {
        METHOD_LOCAL: "Samsung TV — local connection not authorized",
        METHOD_IP_CONTROL: "Samsung TV — IP Control token rejected",
        METHOD_SMARTTHINGS: "Samsung TV — SmartThings refuses this TV",
        METHOD_SMARTTHINGS_PLAN: "Samsung TV — SmartThings API access refused",
    },
    "fr": {
        METHOD_LOCAL: "Téléviseur Samsung — connexion locale non autorisée",
        METHOD_IP_CONTROL: "Téléviseur Samsung — token IP Control rejeté",
        METHOD_SMARTTHINGS: "Téléviseur Samsung — SmartThings refuse ce téléviseur",
        METHOD_SMARTTHINGS_PLAN: "Téléviseur Samsung — accès à l'API SmartThings refusé",
    },
}

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        METHOD_LOCAL: (
            "The Samsung TV **{device_name}** keeps rejecting the local "
            "WebSocket token, so Home Assistant stopped reconnecting to avoid "
            "repeatedly re-triggering the on-screen authorization prompt.\n\n"
            "To fix it: open **Settings → Devices & services → Samsung TV "
            "Smart → Reconfigure → Authentication**, submit your method, and "
            "accept the authorization prompt **once** on the TV.\n\n"
            "This notification clears automatically once the connection is "
            "authorized again."
        ),
        METHOD_IP_CONTROL: (
            "The IP Control access token for **{device_name}** was rejected by "
            "the TV (unauthorized). IP Control power features (including the "
            "reboot button) are paused until it is re-paired.\n\n"
            "To fix it: open **Settings → Devices & services → Samsung TV "
            "Smart → Reconfigure → IP Control**, check *Pair now* while the TV "
            "is ON and **not** in Art Mode, and accept the prompt on "
            "screen.\n\nThis notification clears automatically once IP Control "
            "works again."
        ),
        METHOD_SMARTTHINGS: (
            "SmartThings refuses access to **{device_name}** (*Forbidden*). "
            "The cloud features — channel name, power metering, the ambient "
            "light sensor, Hue Sync — are paused; local control keeps "
            "working.\n\n"
            "Two causes are likely:\n\n"
            "- **No SmartThings API plan.** From **15 December 2026** Samsung "
            "deactivates the API keys of owners who have not chosen a paid "
            "plan (plans launched on 4 November 2026). Every TV on the same "
            "Samsung account is then refused. Choose a plan in the SmartThings "
            "Developer Center, or stop using SmartThings: **Reconfigure → "
            "Authentication → No SmartThings (local only)**. Local control, "
            "Art Mode included, keeps working; set *Power On Method* to "
            "Wake-on-LAN or IP Control.\n"
            "- **The TV is no longer the configured device.** It "
            "re-registers under a new id after a **mainboard repair or "
            "replacement**, a factory reset not yet signed in to your Samsung "
            "account again, being removed and re-added in the SmartThings "
            "app, or a change of owning account. Link it in the SmartThings "
            "app first, then **Reconfigure → SmartThings device** and pick it "
            "again. Your entities and history are kept.\n\n"
            "Polling has been slowed down until it works again, and this "
            "notice clears itself automatically."
        ),
        METHOD_SMARTTHINGS_PLAN: (
            "SmartThings refuses **every TV on this Samsung account**, "
            "**{device_name}** included. That points to the account rather "
            "than to one TV: from **15 December 2026** Samsung deactivates "
            "the API keys of owners who have not chosen a paid SmartThings "
            "API plan (plans launched on 4 November 2026). The cloud "
            "features — channel name, power metering, the ambient light "
            "sensor, Hue Sync — are paused; local control keeps working.\n\n"
            "Either choose a plan in the SmartThings Developer Center, or "
            "stop using SmartThings for each TV: **Reconfigure → "
            "Authentication → No SmartThings (local only)**, and set *Power "
            "On Method* to Wake-on-LAN or IP Control. A Personal Access Token "
            "is no way around it either: it expires after 24 hours.\n\n"
            "If you do have a plan, sign in again under **Reconfigure → "
            "Authentication**.\n\n"
            "Polling has been slowed down until it works again, and this "
            "notice clears itself automatically."
        ),
    },
    "fr": {
        METHOD_LOCAL: (
            "Le téléviseur Samsung **{device_name}** rejette en continu le "
            "token WebSocket local ; Home Assistant a donc cessé de se "
            "reconnecter pour éviter de redéclencher sans cesse l'invite "
            "d'autorisation à l'écran.\n\n"
            "Pour corriger : ouvrez **Paramètres → Appareils et services → "
            "Samsung TV Smart → Reconfigurer → Authentification**, validez "
            "votre méthode, et acceptez l'invite d'autorisation **une fois** "
            "sur le téléviseur.\n\nCette notification disparaît automatiquement "
            "dès que la connexion est de nouveau autorisée."
        ),
        METHOD_IP_CONTROL: (
            "Le token IP Control de **{device_name}** a été rejeté par le "
            "téléviseur (non autorisé). Les fonctions d'alimentation IP "
            "Control (dont le bouton de redémarrage) sont en pause jusqu'au "
            "ré-appairage.\n\n"
            "Pour corriger : ouvrez **Paramètres → Appareils et services → "
            "Samsung TV Smart → Reconfigurer → IP Control**, cochez *Appairer "
            "maintenant* téléviseur ALLUMÉ et **pas** en mode Art, puis "
            "acceptez l'invite à l'écran.\n\nCette notification disparaît "
            "automatiquement dès qu'IP Control refonctionne."
        ),
        METHOD_SMARTTHINGS: (
            "SmartThings refuse l'accès à **{device_name}** (*Forbidden*). "
            "Les fonctions cloud — nom de chaîne, mesure de consommation, "
            "capteur de luminosité ambiante, Hue Sync — sont suspendues ; le "
            "contrôle local continue de fonctionner.\n\n"
            "Deux causes sont probables :\n\n"
            "- **Pas d'abonnement à l'API SmartThings.** À partir du **15 "
            "décembre 2026**, Samsung désactive les clés d'API des comptes "
            "qui n'ont pas choisi d'offre payante (offres lancées le 4 "
            "novembre 2026). Tous les téléviseurs du même compte Samsung sont "
            "alors refusés. Choisissez une offre dans le SmartThings "
            "Developer Center, ou cessez d'utiliser SmartThings : "
            "**Reconfigurer → Authentification → No SmartThings (local "
            "only)**. Le contrôle local, Art Mode compris, continue de "
            "fonctionner ; réglez la *méthode d'allumage* sur Wake-on-LAN ou "
            "IP Control.\n"
            "- **Le téléviseur n'est plus l'appareil configuré.** Il se "
            "réenregistre sous un nouvel identifiant après un **remplacement "
            "de carte mère**, une réinitialisation pas encore reconnectée à "
            "votre compte Samsung, une suppression puis un rajout dans "
            "l'application SmartThings, ou un changement de compte "
            "propriétaire. Associez-le d'abord dans l'application "
            "SmartThings, puis **Reconfigurer → Appareil SmartThings** et "
            "sélectionnez-le à nouveau. Vos entités et votre historique sont "
            "conservés.\n\n"
            "Les interrogations ont été espacées en attendant, et cette "
            "notification disparaît automatiquement."
        ),
        METHOD_SMARTTHINGS_PLAN: (
            "SmartThings refuse **tous les téléviseurs de ce compte "
            "Samsung**, dont **{device_name}**. Cela désigne le compte plutôt "
            "qu'un téléviseur : à partir du **15 décembre 2026**, Samsung "
            "désactive les clés d'API des comptes qui n'ont pas choisi d'offre "
            "payante de l'API SmartThings (offres lancées le 4 novembre "
            "2026). Les fonctions cloud — nom de chaîne, mesure de "
            "consommation, capteur de luminosité ambiante, Hue Sync — sont "
            "suspendues ; le contrôle local continue de fonctionner.\n\n"
            "Choisissez une offre dans le SmartThings Developer Center, ou "
            "cessez d'utiliser SmartThings pour chaque téléviseur : "
            "**Reconfigurer → Authentification → No SmartThings (local "
            "only)**, et réglez la *méthode d'allumage* sur Wake-on-LAN ou IP "
            "Control. Un Personal Access Token ne contourne rien non plus : il "
            "expire au bout de 24 heures.\n\n"
            "Si vous avez une offre, reconnectez-vous sous **Reconfigurer → "
            "Authentification**.\n\n"
            "Les interrogations ont été espacées en attendant, et cette "
            "notification disparaît automatiquement."
        ),
    },
}


def _lang(hass: HomeAssistant) -> str:
    """Pick the notification language (fr when configured, else en)."""
    language = (hass.config.language or "en").lower()
    return "fr" if language.startswith("fr") else "en"


def _notification_id(entry_id: str, method: str) -> str:
    """Build a stable notification id so re-raising never spams the user."""
    return f"{DOMAIN}_token_{method}_{entry_id}"


@callback
def notify_token_problem(
    hass: HomeAssistant,
    entry_id: str,
    method: str,
    device_name: str,
) -> None:
    """Raise (or refresh) the persistent notification for a bad token."""
    lang = _lang(hass)
    if method not in _MESSAGES[lang]:
        return
    persistent_notification.async_create(
        hass,
        _MESSAGES[lang][method].format(device_name=device_name),
        title=_TITLES[lang][method],
        notification_id=_notification_id(entry_id, method),
    )


@callback
def clear_token_problem(hass: HomeAssistant, entry_id: str, method: str) -> None:
    """Dismiss the persistent notification once the token works again."""
    persistent_notification.async_dismiss(hass, _notification_id(entry_id, method))


# entry_id -> (refused, device_name) for every TV whose SmartThings access has
# been checked since startup. Kept outside hass.data[DOMAIN], whose values are
# per-entry dicts.
_ST_ACCESS = f"{DOMAIN}_smartthings_access"


def _st_access(hass: HomeAssistant) -> dict[str, tuple[bool, str]]:
    return hass.data.setdefault(_ST_ACCESS, {})


def _whole_account_refused(access: dict[str, tuple[bool, str]]) -> bool:
    """Two or more SmartThings TVs, and all of them refused.

    One refused TV among working ones is that TV (a new device id after a
    repair); every one refused at once is the account - from 15 December 2026,
    a key deactivated for want of a paid API plan. A single TV cannot tell the
    two apart, so it gets the notice naming both.
    """
    return len(access) >= 2 and all(refused for refused, _ in access.values())


@callback
def _notify_smartthings(
    hass: HomeAssistant, entry_id: str, device_name: str, whole_account: bool
) -> None:
    lang = _lang(hass)
    key = METHOD_SMARTTHINGS_PLAN if whole_account else METHOD_SMARTTHINGS
    persistent_notification.async_create(
        hass,
        _MESSAGES[lang][key].format(device_name=device_name),
        title=_TITLES[lang][key],
        notification_id=_notification_id(entry_id, METHOD_SMARTTHINGS),
    )


@callback
def report_smartthings_access(
    hass: HomeAssistant, entry_id: str, device_name: str, refused: bool
) -> None:
    """Record whether SmartThings accepts this TV and update its notice.

    Called with ``refused=True`` once the refusals pass the threshold, and with
    ``refused=False`` after a successful poll (a no-op when nothing changed).
    """
    access = _st_access(hass)
    previous = access.get(entry_id)
    if previous is not None and previous[0] == refused and not refused:
        return
    before = _whole_account_refused(access)
    access[entry_id] = (refused, device_name)
    after = _whole_account_refused(access)
    if not refused:
        clear_token_problem(hass, entry_id, METHOD_SMARTTHINGS)
    if after:
        for other_id, (_refused, name) in access.items():
            _notify_smartthings(hass, other_id, name, whole_account=True)
        return
    if refused:
        _notify_smartthings(hass, entry_id, device_name, whole_account=False)
    if before:
        # No longer the whole account: the others get the per-TV wording back.
        for other_id, (other_refused, name) in access.items():
            if other_refused and other_id != entry_id:
                _notify_smartthings(hass, other_id, name, whole_account=False)


@callback
def forget_smartthings_access(hass: HomeAssistant, entry_id: str) -> None:
    """Drop a TV that is being unloaded from the account-wide check."""
    _st_access(hass).pop(entry_id, None)
