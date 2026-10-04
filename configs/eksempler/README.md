# Eksempel-configs til flere CNC-styringer

Filerne her er **startpunkter**, ikke færdige indstillinger. Værdierne (baud, databit, paritet,
stopbit, handshake) er de typiske standardværdier for hver styring, men hver maskine kan være
sat anderledes op. Tjek altid maskinens egne RS-232/V24-parametre og ret filen, så de passer
præcist – ellers kommer der enten ingenting eller volapyk igennem.

## Sådan bruges en fil
1. Kopier filen fra `configs/eksempler/` op i `configs/` og giv den maskinens navn,
   f.eks. `configs/haas_vf2.json`. Filnavnet bliver portens navn i menuen og på dashboardet.
2. Ret `"port"`:
   - USB/seriel adapter: `"/dev/ttyUSB0"`, `"/dev/ttyS0"` osv.
   - Moxa: `"socket://IP-ADRESSE:4001"` (Moxa'en skal stå i TCP Server mode, og dens egne
     serielle indstillinger skal matche maskinen).
3. Ret baud/databit/paritet/stopbit/handshake, så de matcher maskinen.
4. Genstart DNC-programmet.

Filerne i `configs/eksempler/` bliver **ikke** startet af programmet – kun `.json`-filer direkte i
`configs/` åbner en port.

## Oversigt (typiske værdier)

| Fil | Styring | Baud | Data | Paritet | Stop | Handshake | Linjeskift | Program-start/slut |
|---|---|---|---|---|---|---|---|---|
| `heidenhain_tnc.json` | Heidenhain TNC (klartekst) | 9600 | 7 | Lige (E) | 1 | XON/XOFF | CR LF | `BEGIN PGM` / `END PGM` |
| `siemens_sinumerik.json` | Siemens 840D/810D/802D/828D | 9600 | 8 | Ingen (N) | 1 | XON/XOFF | LF | `%` / tegnet 1A hex (EOF) |
| `haas.json` | Haas | 9600 | 7 | Lige (E) | 1 | XON/XOFF | CR LF | `%` / `%` |
| `mazak.json` | Mazak (kun EIA/ISO) | 9600 | 7 | Lige (E) | 2 | XON/XOFF | CR LF | `%` / `%` |
| `okuma_osp.json` | Okuma OSP | 9600 | 7 | Lige (E) | 2 | XON/XOFF | CR LF | `%` / `%` |
| `mitsubishi_meldas.json` | Mitsubishi Meldas/M70/M80 | 9600 | 7 | Lige (E) | 2 | XON/XOFF | CR LF | `%` / `%` |

Fanuc findes allerede (template `FANUC`, se `configs/moxa.json`).

## Ting at være opmærksom på
- **Siemens:** Programmer i hulstrimmelformat har ingen afsluttende `%`. Modtagelsen stopper derfor
  på sluttegnet 1A hex. Slå "stop med sluttegn" til i styringens V24-indstillinger. Ellers
  gemmes programmet først som `error_timeout_*.partial` efter `rx_timeout`.
- **Mazak:** Kun EIA/ISO G-kode. Mazatrol-programmer sendes i et binært format og kan ikke
  modtages korrekt.
- **Okuma:** Okuma sender ofte en hovedlinje `$NAVN.MIN%` før programmet. Den del gemmes ikke;
  filen navngives efter programmets O-nummer, ellers efter tidspunktet.
- **Heidenhain:** Kun klartekst-programmer. ISO-programmer på Heidenhain (`.I`) bruger `%`-format;
  brug så en fil som `haas.json` som udgangspunkt i stedet.
- **Handshake:** Hvis maskinen bruger hardware-handshake (RTS/CTS) i stedet for XON/XOFF, så sæt
  `"rtscts": true` og `"xonxoff": false`. Kablet skal så have RTS/CTS forbundet.
- `"_kommentar"` i hver fil er kun en note til dig; programmet ignorerer den.

## Alle felter en config kan have
`port`, `template` (`FANUC`, `HEIDENHAIN`, `SIEMENS`, `DEFAULT`), `baudrate`, `bytesize` (7/8),
`parity` (`N`/`E`/`O`), `stopbits` (1/2), `xonxoff`, `rtscts`, `encoding`, `start_trigger`,
`end_trigger`, `min_content_length`, `rx_timeout` (sekunder), `filename_pattern`,
`request_pattern`, `leader_nulls`, `trailer_nulls`, `eol` (`CR`/`LF`/`CRLF`).
Felter der ikke står i filen, tages fra den valgte template.
