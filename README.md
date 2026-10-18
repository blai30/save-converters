# save-converters

Scripts for moving game saves across platforms (console to PC so far), for games where copying the console save over doesn't work. Each converter was built by reverse engineering both platforms' save formats and has been verified with real saves that load and play.

## Supported conversions

### PlayStation

| Game | From | To |
| --- | --- | --- |
| [Death Stranding Director's Cut](games/playstation/death-stranding/) | PS5 | PC (Steam) |
| [Devil May Cry 5](games/playstation/devil-may-cry-5/) | PS4, PS5 | PC (Steam) |
| [Final Fantasy XVI](games/playstation/final-fantasy-xvi/) | PS5 | PC (Steam) |
| [Ghost of Tsushima Director's Cut](games/playstation/ghost-of-tsushima/) | PS5 | PC (Steam) |
| [God of War (2018)](games/playstation/god-of-war/) | PS4 | PC (Steam) |
| [God of War Ragnarok](games/playstation/god-of-war-ragnarok/) | PS5 | PC (Steam) |
| [Horizon Forbidden West](games/playstation/horizon-forbidden-west/) | PS5 | PC (Steam) |
| [Tales of Arise](games/playstation/tales-of-arise/) | PS5 | PC (Steam) |

### Nintendo Switch

| Game | From | To |
| --- | --- | --- |
| [Atelier Lulua](games/switch/atelier-lulua/) | Switch | PC (Steam) |
| [Mega Man 11](games/switch/mega-man-11/) | Switch | PC (Steam) |
| [One Step From Eden](games/switch/one-step-from-eden/) | Switch | PC (Steam) |
| [Overcooked! All You Can Eat](games/switch/overcooked-all-you-can-eat/) | Switch (Overcooked! Special Edition, Overcooked! 2) | PC (Steam) |

## Requirements

- Python 3.10 or newer
- `pip install -r requirements.txt`
- Saves exported from the console. PlayStation saves must be **decrypted** first (for example with Apollo Save Tool or a similar save manager on a jailbroken console); Switch saves can come straight from a save manager such as JKSV. These scripts don't decrypt PlayStation save containers.

## Usage

1. Drop your saves into `saves/` (it's gitignored, so nothing in it gets committed). Any folder structure works.
2. Follow the README in the game's folder under `games/`.
3. Back up your existing PC saves before copying converted files over them.

## Running the tests

```sh
python -m unittest discover -s games/playstation/god-of-war
python -m unittest discover -s games/playstation/god-of-war-ragnarok
python -m unittest discover -s games/switch/overcooked-all-you-can-eat
```

The tests use synthetic data, so they don't need any real saves.

## Contributing

Each game lives in `games/<source platform>/<game>/` with its converter and a README that covers the recipe and the format notes. New games and platforms are welcome.

## License

[MIT](LICENSE)
