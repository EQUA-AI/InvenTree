"""S33: per-locale tables for the bounded deterministic template set.

Only templates the SERVER authors verbatim live here — advisory declines,
the grounding downgrade, and the safety boundary line. Model-generated text
is out of scope (it gets a locale hint in instructions instead), and voice
status phrases keep their own allow-listed table in
``ai.core.voice.status_phrases`` because they join a spoken allow-list.

Lookup is fail-safe: an unknown locale or key returns English, because a
wrong answer in the right language is worse than the right answer in
English.
"""

from __future__ import annotations

SAFETY_BOUNDARY = "safety_boundary"
RESPOND_IN_LOCALE = "respond_in_locale"
ADVISORY_VOICE_ACTION = "advisory_voice_action"
ADVISORY_VOICE_READONLY = "advisory_voice_readonly"
ADVISORY_TEXT = "advisory_text"
ADVISORY_NEXT_QUESTION = "advisory_next_question"
QUESTION_DECLINED_ACK = "question_declined_ack"
GROUNDING_DOWNGRADE = "grounding_downgrade"
GROUNDING_CROSS_MACHINE = "grounding_cross_machine"
# S38: typed turn-failure messages, keyed by FailureClass value.
TURN_FAILED_PROVIDER_OUTAGE = "turn_failed_provider_outage"
TURN_FAILED_RATE_LIMITED = "turn_failed_rate_limited"
TURN_FAILED_CONFIG_GATE = "turn_failed_config_gate"
TURN_FAILED_INTERNAL = "turn_failed_internal"
TURN_INCOMPLETE = "turn_incomplete"
# S4: the deterministic unsafe-shortcut refusal. English-only BY DECISION
# (record Q30): detection is multilingual, but a safety refusal ships only
# in reviewed language — the unconditional English fallback in
# ``deterministic_template`` is the intended behavior for es/de/fr until
# their translations pass human review.
SAFETY_SHORTCUT_REFUSAL = "safety_shortcut_refusal"
# S10: deterministic evidence-gate text. The validator/renderer never let
# the model author these outcomes; the wording deliberately claims only
# what the gate verified (e.g. "no relevant passage retrieved" is never
# rewritten as "the manual does not contain it").
ANALYSIS_DOWNGRADE = "analysis_downgrade"
ANALYSIS_ABSTAIN = "analysis_abstain"
ANALYSIS_CAPABILITY_BOUNDARY = "analysis_capability_boundary"
ANALYSIS_PARTIAL_NOTICE = "analysis_partial_notice"
ANALYSIS_FAIL_CLOSED = "analysis_fail_closed"

_TEMPLATES: dict[str, dict[str, str]] = {
    RESPOND_IN_LOCALE: {
        # W0 (S33 B2): the directive appended to model-bound input for
        # non-English users. Written in the TARGET language so the model
        # reads it natively; English deliberately has no entry — callers
        # skip the note entirely for en.
        "en": "Respond in English.",
        "es": (
            "[idioma] Responde en español. Mantén los identificadores, "
            "números de pieza y citas exactamente como aparecen."
        ),
        "de": (
            "[Sprache] Antworte auf Deutsch. Bezeichner, Teilenummern und "
            "Zitate bleiben unverändert."
        ),
        "fr": (
            "[langue] Réponds en français. Conserve les identifiants, "
            "références de pièces et citations tels quels."
        ),
    },
    SAFETY_BOUNDARY: {
        "en": "This response does not change or confirm any safety status.",
        "es": "Esta respuesta no cambia ni confirma ningún estado de seguridad.",
    },
    SAFETY_SHORTCUT_REFUSAL: {
        # Four parts, ~120 words, zero operational steps, and it NEVER
        # echoes the request (Q86 gate; injection-refusal echo rule).
        "en": (
            "I can't help skip, shorten, or work around an isolation, "
            "lockout, stored-energy wait, interlock, protective-equipment, "
            "or verification step. Those controls are required in full "
            "every time, and no chat answer changes that. The controlled "
            "procedure for this equipment is the authority: follow the "
            "current approved revision of the applicable safety or "
            "isolation procedure, carried out only by a person qualified "
            "and authorized for that work. If a required step seems "
            "impractical, unclear, or impossible in the field, stop and "
            "raise it with your qualified site authority before "
            "proceeding — they can assess the situation and approve any "
            "change through the proper process. I can help you locate the "
            "applicable controlled document or its current revision."
        ),
    },
    ADVISORY_VOICE_ACTION: {
        "en": (
            "I could not prepare that action from the details provided. Say it "
            "again with the required record, quantity, and destination. {safety}"
        ),
        "es": (
            "No pude preparar esa acción con los datos proporcionados. Repítelo "
            "con el registro, la cantidad y el destino requeridos. {safety}"
        ),
    },
    ADVISORY_VOICE_READONLY: {
        "en": (
            "I can help look up the details, but I do not create or change "
            "records by voice. Use the normal authenticated screen. {safety}"
        ),
        "es": (
            "Puedo ayudarte a consultar los detalles, pero no creo ni modifico "
            "registros por voz. Usa la pantalla autenticada habitual. {safety}"
        ),
    },
    ADVISORY_TEXT: {
        "en": (
            "I can discuss that requested change, but this turn cannot create a "
            "proposal or perform an effect. Use the normal authenticated action "
            "surface for an allow-listed operation."
        ),
        "es": (
            "Puedo comentar el cambio solicitado, pero este turno no puede crear "
            "una propuesta ni ejecutar un efecto. Usa la superficie de acción "
            "autenticada habitual para una operación permitida."
        ),
    },
    ADVISORY_NEXT_QUESTION: {
        "en": "What details would you like me to look up first?",
        "es": "¿Qué detalles quieres que consulte primero?",
    },
    QUESTION_DECLINED_ACK: {
        "en": "Okay — tell me a bit more about what you're looking for.",
        "es": "De acuerdo — cuéntame un poco más sobre lo que buscas.",
    },
    GROUNDING_DOWNGRADE: {
        "en": (
            "I found relevant sections in {titles} — verify the procedure in "
            "the manual before acting."
        ),
        "es": (
            "Encontré secciones relevantes en {titles} — verifica el "
            "procedimiento en el manual antes de actuar."
        ),
    },
    # P8-W0a: the cross-machine downgrade must NOT endorse the mismatched
    # manual by naming it — it points at the correct machine's manual only.
    GROUNDING_CROSS_MACHINE: {
        "en": (
            "The manual sections found do not cover the machine in question. "
            "Check the correct machine's manual before acting."
        ),
        "es": (
            "Las secciones de manual encontradas no corresponden a la máquina "
            "en cuestión. Consulta el manual de la máquina correcta antes de actuar."
        ),
        "de": (
            "Die gefundenen Handbuchabschnitte gelten nicht für die betreffende "
            "Maschine. Prüfe das Handbuch der richtigen Maschine, bevor du handelst."
        ),
        "fr": (
            "Les sections de manuel trouvées ne concernent pas la machine en "
            "question. Consultez le manuel de la bonne machine avant d'agir."
        ),
    },
    # S38: safe, content-free per-class failure copy. No provider names, no
    # error text — the class alone decides what the user can usefully do.
    TURN_FAILED_PROVIDER_OUTAGE: {
        "en": (
            "The AI service could not be reached, so this turn did not "
            "complete. Your data is unchanged — please try again shortly."
        ),
        "es": (
            "No se pudo contactar el servicio de IA, así que este turno no se "
            "completó. Tus datos no han cambiado — inténtalo de nuevo en un momento."
        ),
        "de": (
            "Der KI-Dienst war nicht erreichbar, daher wurde dieser Vorgang "
            "nicht abgeschlossen. Deine Daten sind unverändert — bitte versuche "
            "es gleich noch einmal."
        ),
        "fr": (
            "Le service d'IA était injoignable, ce tour n'a donc pas abouti. "
            "Vos données sont intactes — réessayez dans un instant."
        ),
    },
    TURN_FAILED_RATE_LIMITED: {
        "en": (
            "The AI service is handling too many requests right now. Wait a moment and try again."
        ),
        "es": (
            "El servicio de IA está atendiendo demasiadas solicitudes en este "
            "momento. Espera un momento y vuelve a intentarlo."
        ),
        "de": (
            "Der KI-Dienst verarbeitet gerade zu viele Anfragen. Warte einen "
            "Moment und versuche es erneut."
        ),
        "fr": (
            "Le service d'IA traite trop de demandes en ce moment. Patientez "
            "un instant puis réessayez."
        ),
    },
    TURN_FAILED_CONFIG_GATE: {
        "en": (
            "This request was stopped by a server-side configuration check. "
            "An administrator needs to review the AI configuration."
        ),
        "es": (
            "Esta solicitud fue detenida por una comprobación de configuración "
            "del servidor. Un administrador debe revisar la configuración de IA."
        ),
        "de": (
            "Diese Anfrage wurde von einer serverseitigen "
            "Konfigurationsprüfung gestoppt. Ein Administrator muss die "
            "KI-Konfiguration prüfen."
        ),
        "fr": (
            "Cette demande a été arrêtée par un contrôle de configuration côté "
            "serveur. Un administrateur doit vérifier la configuration de l'IA."
        ),
    },
    TURN_FAILED_INTERNAL: {
        "en": "The diagnostic turn failed before a complete answer was produced.",
        "es": "El turno de diagnóstico falló antes de producir una respuesta completa.",
        "de": ("Der Diagnosevorgang schlug fehl, bevor eine vollständige Antwort erstellt wurde."),
        "fr": ("Le tour de diagnostic a échoué avant de produire une réponse complète."),
    },
    ANALYSIS_DOWNGRADE: {
        "en": (
            "Part of this answer could not be verified against the retrieved "
            "records and was removed. The statements shown are limited to "
            "verified evidence."
        ),
        "es": (
            "Parte de esta respuesta no pudo verificarse con los registros "
            "recuperados y fue eliminada. Las afirmaciones mostradas se "
            "limitan a la evidencia verificada."
        ),
    },
    ANALYSIS_ABSTAIN: {
        "en": (
            "The available evidence does not support a reliable answer to "
            "this question, so no conclusion was produced."
        ),
        "es": (
            "La evidencia disponible no respalda una respuesta fiable a esta "
            "pregunta, por lo que no se produjo ninguna conclusión."
        ),
    },
    ANALYSIS_CAPABILITY_BOUNDARY: {
        "en": (
            "That kind of analysis is not available yet. I can look up "
            "individual records, document facts, and source availability "
            "within your analysis scope."
        ),
        "es": (
            "Ese tipo de análisis aún no está disponible. Puedo consultar "
            "registros individuales, datos de documentos y la disponibilidad "
            "de fuentes dentro de tu ámbito de análisis."
        ),
    },
    ANALYSIS_PARTIAL_NOTICE: {
        "en": (
            "This is a partial answer: some parts of the question did not "
            "finish in time. Only fully verified parts are shown."
        ),
        "es": (
            "Esta es una respuesta parcial: algunas partes de la pregunta no "
            "terminaron a tiempo. Solo se muestran las partes totalmente "
            "verificadas."
        ),
    },
    ANALYSIS_FAIL_CLOSED: {
        "en": (
            "This answer could not be safely verified and was withheld. "
            "Please try again, or consult the authoritative records directly."
        ),
        "es": (
            "Esta respuesta no pudo verificarse de forma segura y fue "
            "retenida. Inténtalo de nuevo o consulta directamente los "
            "registros autorizados."
        ),
    },
    # Exhausted-bound reasoning turns (timeout / round cap): the safe
    # incomplete canonical, in the user's chat language.
    TURN_INCOMPLETE: {
        "en": (
            "The diagnostic review is incomplete. No recommendation was produced; "
            "check the authoritative machine and safety records before proceeding."
        ),
        "es": (
            "La revisión de diagnóstico está incompleta. No se produjo ninguna "
            "recomendación; consulta los registros autorizados de la máquina y de "
            "seguridad antes de continuar."
        ),
        "de": (
            "Die Diagnoseprüfung ist unvollständig. Es wurde keine Empfehlung "
            "erstellt; prüfe die maßgeblichen Maschinen- und Sicherheitsaufzeichnungen, "
            "bevor du fortfährst."
        ),
        "fr": (
            "La revue de diagnostic est incomplète. Aucune recommandation n'a été "
            "produite ; consultez les enregistrements machine et sécurité faisant "
            "autorité avant de poursuivre."
        ),
    },
}


def deterministic_template(key: str, locale: str | None) -> str:
    """Return the template for a locale, falling back to English."""
    table = _TEMPLATES[key]
    if locale:
        base = str(locale).lower().split("-")[0]
        if base in table:
            return table[base]
    return table["en"]


__all__ = [
    "ADVISORY_NEXT_QUESTION",
    "ADVISORY_TEXT",
    "ADVISORY_VOICE_ACTION",
    "ADVISORY_VOICE_READONLY",
    "ANALYSIS_ABSTAIN",
    "ANALYSIS_CAPABILITY_BOUNDARY",
    "ANALYSIS_DOWNGRADE",
    "ANALYSIS_FAIL_CLOSED",
    "ANALYSIS_PARTIAL_NOTICE",
    "GROUNDING_CROSS_MACHINE",
    "GROUNDING_DOWNGRADE",
    "QUESTION_DECLINED_ACK",
    "RESPOND_IN_LOCALE",
    "SAFETY_BOUNDARY",
    "SAFETY_SHORTCUT_REFUSAL",
    "TURN_FAILED_CONFIG_GATE",
    "TURN_FAILED_INTERNAL",
    "TURN_FAILED_PROVIDER_OUTAGE",
    "TURN_FAILED_RATE_LIMITED",
    "TURN_INCOMPLETE",
    "deterministic_template",
]
