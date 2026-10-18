# save-converters

Public repo of cross-platform game save converters (Python, stdlib plus the pip packages in `requirements.txt`; no native or hand-built dependencies).

## Layout

- `games/<source platform>/<game>/`: one flat folder per game (`playstation/`, `switch/`) with the converter, its helper modules, optional `test_*.py` (unittest, synthetic data only) and a README with the working recipe and format notes. Modules import each other by plain name, so run scripts from or point tests at that folder.
- `saves/`: gitignored. Real saves, converter outputs and private research, split by platform.

## Rules

- Publish a converter for every game whose saves don't work on the target platform as-is and whose converted saves work. Games that only need a copy or rename (e.g. Spider-Man) stay out of the repo. Keep research scripts, disassembly dumps and anything derived from game executables in `saves/`.
- Never write to real save locations (`Saved Games`, `%LOCALAPPDATA%`, Documents, Steam `userdata`). Write outputs under `saves/`.
- Reading installed game files is fine; never launch a game.
- Verify converter changes by reconverting real inputs in `saves/` and comparing against known-good outputs.
- Published files must not contain personal data (account ids, names, local paths).

## Tests

```sh
python -m unittest discover -s games/playstation/god-of-war
python -m unittest discover -s games/playstation/god-of-war-ragnarok
```
