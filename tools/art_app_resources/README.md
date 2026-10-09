# Art app text resources → entity translations

The Frame's art app (`org.tizen.art-app`) ships its UI strings for 91 locales in
`bin/<locale>/ArtAppTextResources.resources.dll`. The matte type / matte color
select states, the motion-timer durations and the Art Mode switch name are
taken from there, so Home Assistant shows the same labels as the TV (e.g.
`seafoam` → "Slate", `flexible` → "Modern Mat - Auto").

```sh
pip install dnfile
python3 tools/art_app_resources/extract_resources.py <dump>/org.tizen.art-app/bin /tmp/art_strings.json
python3 tools/art_app_resources/gen_translations.py /tmp/art_strings.json \
    custom_components/samsungtv_smart/translations \
    custom_components/samsungtv_smart/strings.json
```

`gen_translations.py` only merges the `entity` keys it owns; everything else in
the translation files is left untouched. Strings the art app does not contain
(motion timer name, `off` / `always`) are in its `MANUAL` table. The id → string
mapping comes from `MatteControl` in `ArtDataManagement.dll`; see
`notes/T-PTMDDEUC/ART_APP_3.50.106.md`.
