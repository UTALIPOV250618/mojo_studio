# Fonts

Not bundled yet. Before release, add the static `.ttf` files here from the official releases
(both SIL Open Font License 1.1; include the license texts next to the fonts):

- Inter (`Inter-*.ttf`): https://github.com/rsms/inter/releases
- JetBrains Mono (`JetBrainsMono-*.ttf`): https://github.com/JetBrains/JetBrainsMono/releases

`load_fonts()` registers every `*.ttf` found here; without them Qt falls back to Segoe UI.
