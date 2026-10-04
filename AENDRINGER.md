# Multi DNC v2.1 – ændringer i forhold til v2.0

## Fejlrettelser
1. **Moxa/socket-afbrydelse giver nu genforbindelse.** Før blev "read failed: socket disconnected"
   fanget inde i læsningen, porten blev aldrig lukket, og loggen fik ~1000 fejllinjer (se den gamle
   dnc_system.log). Nu lukkes porten og der forbindes igen efter 2 sek.
2. **Tom fil i out/ blokerer ikke længere porten.** Før stod en 0-byte fil forrest i køen for evigt,
   og porten hverken sendte andet eller modtog. Nu flyttes den til aborted/ efter 10 sek.
3. **Halvt kopierede filer sendes ikke.** "send"/fjernforespørgsel kopierer via en midlertidig fil,
   og filer lagt i out/ i hånden skal være 2 sek. gamle før de sendes.
4. **Filnavne med kolon.** Fanuc ':1236' gemmes nu som O1236.nc (kolon er ulovligt på Windows/SMB).
5. **Store/små bogstaver.** "send moxa o1236" finder nu O1236.nc, og /R/o1236 genkendes også.
6. **DEFAULT-skabelonen var tom** (kun eol). Den har nu alle felter; ukendt template-navn giver en advarsel.
7. **start_trigger/end_trigger i JSON** (tekst) konverteres til bytes – før gav det en TypeError ved modtagelse.
8. **"Kunne ikke åbne port"** logges én gang i minuttet i stedet for tilfældigt (time % 10).
9. **stop** på en socket://-port fejler ikke længere (reset_output_buffer understøttes ikke dér).
10. Programmet skifter selv til sin egen mappe ved start, så configs/, data/ og loggen findes også
    når det køres som systemd-service.
11. Menuen svarer "Unknown port." / "Unknown command" i stedet for at tie.

## dashboard.html
- "Program ... queued"-linjer (logget fra hovedtråden) vises nu på den rigtige maskine i stedet for "MainThread".
- Logtekst HTML-escapes før visning.

## Ikke ændret
Konfigurationsfilerne er uændrede. Mapperne data/ og dnc_system.log er IKKE med i zip'en,
så du kan pakke ud oven i din installation uden at overskrive dine programmer.
