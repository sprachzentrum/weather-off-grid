# Mitmachen

Danke für dein Interesse am Projekt! Hier findest du alles, was du brauchst, um beizutragen.

## Was wir suchen

- **Stationstests:** Du hast ein Ecowitt-Modell, das hier noch nicht gelistet ist? Teste das Dashboard und berichte, ob alles funktioniert.
- **Übersetzungen:** Neue Sprachen oder Verbesserungen an bestehenden (DE, EN, ES).
- **Off-Grid-Features:** Bessere Algorithmen für Solarertrag, Windpotenzial, Hydro-Prognose.
- **Barrierefreiheit:** ARIA-Labels, Tastaturnavigation, Kontrastverhältnisse.
- **Bugfixes und Verbesserungen:** Immer willkommen.

## Wie du beitragen kannst

1. **Fork** dieses Repository
2. Erstelle einen **Feature-Branch** (`git checkout -b feature/mein-feature`)
3. **Commit** deine Änderungen (`git commit -m "Add: kurze Beschreibung"`)
4. **Push** auf deinen Fork (`git push origin feature/mein-feature`)
5. Öffne einen **Pull Request**

## Richtlinien

- Das Dashboard bleibt eine **einzelne HTML-Datei** (+ config.js). Kein Build-System, keine Frameworks.
- Externe Libraries nur via CDN und nur wenn wirklich nötig.
- Code-Kommentare auf **Englisch** (Deutsch ist OK für UI-Strings und Doku).
- Teste auf Handy (360px) und Desktop (1200px+).
- Kein Tracking, keine Analytics, keine Cookies.

## Commit-Nachrichten

```
Add: neues Feature
Fix: Bugfix
Docs: Dokumentation
Style: CSS/Formatierung (kein Logik-Change)
Refactor: Code-Umbau ohne Feature-Änderung
Test: Tests hinzufügen/ändern
```

## Fragen?

Öffne ein [Issue](../../issues) oder starte eine [Discussion](../../discussions).
