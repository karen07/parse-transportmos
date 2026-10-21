# Moscow Transport Map v0.12.3 - ASCII interface strings

## Changes since v0.12.2

- Replaced escaped Russian labels in the generated JavaScript with plain
  English ASCII labels: `Bus`, `Tram`, `Trolleybus`, `Metro`, `MCC`, and
  `MCD + Trains`, plus `Train` and `MCD` in the route details.
- Replaced the localized mode heading/hint with simple English text.
- Removed the HTML numeric entity -> Unicode -> JavaScript-escaped-string
  machinery previously used solely for these interface labels.
- Removed the Cyrillic example from a generated JavaScript comment.
- Added a regression assertion that the JavaScript templates do not contain
  Unicode escape sequences for static interface text.
- Preserved the OSM names, including Russian names. They are inside the
  existing compressed Base64 transport payload, not JavaScript source text.
- All routing, station access, geometry, caches and default controls are
  unchanged from v0.12.2. The default is still zero transfers, maximum two;
  the combined MCD + Trains switch is off by default.

## Use

```
tar -xf transport-map-osmlab-v0.12.3.tar
./build-transport-map.py --self-test
./build-transport-map.py --report-json report.json
./format-go
```

The standalone output `routes.html` is emitted as ASCII and includes one
self-contained compressed OSM payload. Russian station names remain available
at runtime after the browser decompresses the payload.

This release targets Unicode introduced by JavaScript minification of static
localized strings. It has not been run through the installed `minify` binary
or against the full Moscow PBF in this environment. If `format-go` still
reports non-ASCII after minification, inspect the actual changed characters
rather than adding another blanket encoder.

## Local verification

- Python `--self-test`: pass.
- Generated synthetic standalone HTML: ASCII-only, line length \<= 100.
- Node.js `--check` on the generated JavaScript: pass.
- Synthetic Cyrillic station name survives Base64/gzip and JSON decoding.
- Real OSM PBFs, real `minify` executable and complete browser run were not
  tested in this environment.
