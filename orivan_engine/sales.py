"""Local sales preparation: deterministic templates, no model or paid service."""

import json
from datetime import date

from .core import draft_for, opportunity_for


def acquisition_pack(lead):
    result = json.loads(lead["audit_json"] or "{}")
    if not result or result.get("error"):
        return (f'#{lead["id"]} {lead["name"]}\n'
                'Zuerst Website und Bedarf manuell klären. Für ein konkretes Angebot '
                'liegt noch kein belastbarer Website-Befund vor.')
    observation, idea = opportunity_for(result)
    return (
        f'#{lead["id"]} – Akquise-Paket\n\n{draft_for(lead, result)}\n\n'
        'Einstiegsangebot: Eine kurze Startseitenanalyse mit drei konkreten '
        'Verbesserungsideen. Den Umfang auf eine Seite begrenzen.\n\n'
        'Gesprächsfragen:\n'
        '1. Welche Anfragen soll Ihre Website auslösen?\n'
        '2. Wie kommen diese Anfragen heute bei Ihnen an?\n'
        '3. Wer entscheidet über Änderungen an der Website?\n\n'
        'Textvorschlag für eine angefragte Analyse – Befund vor Verwendung bestätigen:\n'
        f'„Bei der automatischen Startseitenprüfung ist Folgendes aufgefallen: {observation} '
        f'Mein Vorschlag: {idea} Welche Anfragen möchten Sie über Ihre Website gewinnen? '
        'Danach können wir einen passenden, klar abgegrenzten nächsten Schritt besprechen.“\n\n'
        'Nach dem Gespräch: Ergebnis als /notiz speichern und den vereinbarten '
        'Termin mit /wiedervorlage setzen. Preise erst nach Klärung des Umfangs anbieten.'
    )


MARKETING_TOPICS = (
    ('Kontaktweg', 'Öffne deine Website auf dem Smartphone: Findest du sofort, wie du '
     'eine Anfrage stellen kannst? Prüfe Telefonnummer, Kontaktformular und den Weg '
     'dorthin. Ein gut sichtbarer nächster Schritt gehört auf jede wichtige Seite.'),
    ('Klares Angebot', 'Verstehen Besucher auf deiner Startseite, was du anbietest, '
     'für wen dein Angebot gedacht ist und in welcher Region du arbeitest? '
     'Lies die erste Überschrift einmal ohne Vorwissen. Wird das Angebot konkret?'),
    ('Mobile Bedienung', 'Teste deine Website mit einer Hand auf dem Smartphone: '
     'Sind Texte lesbar und Schaltflächen leicht zu treffen? Lässt sich das Menü '
     'öffnen und eine Anfrage ohne mühsames Zoomen abschicken?'),
    ('Vertrauen', 'Echte Einblicke helfen Besuchern, deinen Betrieb einzuschätzen: '
     'Wer steckt dahinter, wie läuft die Zusammenarbeit ab und welche Leistungen '
     'bietet ihr konkret an? Zeige freigegebene eigene Beispiele und klare Ansprechpartner.'),
    ('Anfragen vereinfachen', 'Wie viel müssen Interessenten ausfüllen, bevor sie dir '
     'eine erste Frage stellen können? Prüfe dein Anfrageformular und halte den '
     'ersten Schritt überschaubar. Weitere Details könnt ihr im Gespräch klären.'),
    ('Referenzen erklären', 'Ein Projektbild wird hilfreicher, wenn du erklärst, '
     'welche Aufgabe dahinterstand und wie ihr sie gelöst habt. Für ein eigenes '
     'Beispiel reichen oft drei Sätze: Ausgangslage, Umsetzung, Ergebnis.'),
    ('Website-Selbsttest', 'Drei Fragen für deine Startseite: Ist das Angebot klar? '
     'Funktioniert die Bedienung auf dem Smartphone? Ist der nächste Kontaktweg '
     'sofort sichtbar? Gehe die Seite einmal wie ein neuer Besucher durch.'),
)


def marketing_draft(day):
    title, body = MARKETING_TOPICS[date.fromisoformat(day).weekday()]
    return (
        f'Marketingvorlage – {title}\n\n{body}\n\n'
        'Du möchtest einen zweiten Blick auf deine Startseite? Schreib uns „CHECK“. '
        'Orivan aus Passau schaut sich deine Startseite an und nennt dir drei '
        'konkrete Verbesserungsansätze – kostenlos.\n\n'
        '#Orivan #Passau #Webentwicklung\n\n'
        'Umsetzung: Eine eigene Bildschirmaufnahme oder ein neutrales Beispiel '
        'verwenden. Die Themen rotieren wöchentlich; vor Veröffentlichung mit '
        'einem aktuellen eigenen Beispiel ergänzen. Eingehende CHECK-Anfragen '
        'beantworten und die Analyse auf eine Startseite begrenzen.'
    )
