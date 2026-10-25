# 🤖 Frame Art Automations - Cookbook

Ready-to-adapt automations, scripts and helpers built on top of this
integration, plus the platform pitfalls each one runs into.

Every recipe uses placeholders you must replace:

| Placeholder | Where to find it |
|---|---|
| `<tv>` | the slug of your Frame's entities, e.g. `samsung_hacs` in `switch.samsung_hacs_art_mode` |
| `<ENTRY_ID>` | `{{ state_attr('sensor.<tv>_store', 'path') }}` in **Developer tools → Template** → `/config/www/frame_art/<ENTRY_ID>/store/` |
| `<media_player>` | your Frame's `media_player` entity |

> **Entity ids are not guessable.** Several entities are named from a
> *translated* name, and Home Assistant freezes an entity id at creation time.
> A Frame set up before the integration translated `switch.art_mode` keeps
> `switch.<tv>_art_mode`; one set up after gets `switch.<tv>_mode_art` in
> French. Entities whose name is hardcoded in the integration
> (`_art_mode_brightness`, `_art_mode_color_temperature`, `_brightness_sensor`,
> `_art_metadata`) are always English. List yours before copying anything:
>
> ```jinja
> {{ device_entities(device_id('<media_player>')) | join('\n') }}
> ```

---

## Table of Contents

- [Ambient light sensor drives Art Mode brightness](#ambient-light-sensor-drives-art-mode-brightness)
- [Slideshow with a selectable source](#slideshow-with-a-selectable-source)
- [Random artwork from favourites and personal photos](#random-artwork-from-favourites-and-personal-photos)
- [AI-chosen matte after every artwork change](#ai-chosen-matte-after-every-artwork-change)
- [A curated selection, as a folder of symlinks](#a-curated-selection-as-a-folder-of-symlinks)
- [Platform pitfalls](#platform-pitfalls)

---

## Ambient light sensor drives Art Mode brightness

The Frame's built-in sensor reads the light falling *on the panel*, not the
light in the room. On a wall facing a window it under-reads at the worst
moment. An external lux sensor does better — but **the TV's own sensor must be
turned off first**, or it keeps overwriting whatever you write.

```yaml
automation:
  - alias: Frame art brightness follows the room
    triggers:
      - trigger: state
        entity_id: sensor.living_room_illuminance
        for: "00:02:00"          # don't chase every passing cloud
      - trigger: state
        entity_id: switch.<tv>_art_mode
        to: "on"                 # re-apply when art comes back on
    conditions:
      - condition: state
        entity_id: switch.<tv>_art_mode
        state: "on"
    actions:
      - variables:
          lux: "{{ states('sensor.living_room_illuminance') | float(0) }}"
          target: >-
            {% if   lux <  10 %}10
            {% elif lux <  40 %}20
            {% elif lux < 100 %}40
            {% elif lux < 250 %}60
            {% elif lux < 600 %}80
            {% else %}100{% endif %}
      - condition: template          # skip no-op writes
        value_template: >-
          {{ target | int != states('number.<tv>_art_mode_brightness') | int(-1) }}
      - action: number.set_value
        target:
          entity_id: number.<tv>_art_mode_brightness
        data:
          value: "{{ target | int }}"
    mode: restart
```

**Set `select.<tv>_brightness_sensor` to `off`** before enabling this, and keep
`button.<tv>_art_mode_brightness_reset` in mind as the way back to the TV's own
behaviour.

Notes:

- The slider is 0–100 but the TV has only 10 real steps, so stick to multiples
  of 10 — anything else just rounds.
- `number.<tv>_art_mode_brightness` is deliberately `unavailable` whenever art
  is not on screen: outside Art Mode the TV returns bogus values (a standby
  read of `brightness=10` shows up as a phantom 100%). Hence the
  re-apply trigger on art mode turning on.
- `number.<tv>_art_mode_color_temperature` (−5 warm → +5 cool) does more for
  "looks like a real print" after sunset than brightness alone.

If SmartThings is configured and your Frame publishes its *Light Sensor* child
device, you also get the TV's own reading as `sensor.*_illuminance`, which is
useful to compare against the external one.

---

## Slideshow with a selectable source

A slideshow that picks a random artwork from the **favourites** folder
(`SAM-*`), the **personal** folder (`MY_*`), both, or a curated selection.

### Helpers

| Type | Name | Options |
|---|---|---|
| Toggle | Slideshow | — |
| Dropdown | Slideshow interval | `5` `10` `15` `30` `60` `120` `240` |
| Dropdown | Slideshow source | `Favourites` `Personal` `Both` `Selection` |

### Automation

```yaml
automation:
  - alias: 'Frame Art: slideshow'
    mode: restart
    triggers:
      - trigger: state
        entity_id: input_boolean.slideshow
        to: 'on'
      - trigger: state
        entity_id: input_select.slideshow_source   # apply a source change at once
    conditions:
      - condition: state
        entity_id: input_boolean.slideshow
        state: 'on'
    actions:
      - if:
          - condition: state
            entity_id: switch.<tv>_power
            state: 'off'
        then:
          - action: switch.turn_on
            target:
              entity_id: switch.<tv>_power
      - repeat:
          while:
            - condition: state
              entity_id: input_boolean.slideshow
              state: 'on'
          sequence:
            - variables:
                content_ids: >
                  {% set src = states('input_select.slideshow_source') %}
                  {% set ns = namespace(files=[], ids=[]) %}
                  {% if src == 'Selection' %}
                    {% set ns.files = ns.files
                       + (state_attr('sensor.selection_<tv>', 'file_list') | default([], true)) %}
                  {% endif %}
                  {% if src in ['Favourites', 'Both'] %}
                    {% set ns.files = ns.files
                       + (state_attr('sensor.<tv>_store', 'file_list') | default([], true)) %}
                  {% endif %}
                  {% if src in ['Personal', 'Both'] %}
                    {% set ns.files = ns.files
                       + (state_attr('sensor.<tv>_personal', 'file_list') | default([], true)) %}
                  {% endif %}
                  {% for file in ns.files %}
                    {% set filename = file.split('/')[-1] if '/' in file else file %}
                    {% set content_id = filename.replace('.jpg', '').replace('.JPG', '').replace('.jpeg', '').replace('.JPEG', '').strip() %}
                    {% if content_id.startswith('SAM-') or content_id.startswith('MY') %}
                      {% set ns.ids = ns.ids + [content_id] %}
                    {% endif %}
                  {% endfor %}
                  {{ ns.ids }}
            - condition: template
              value_template: '{{ content_ids | length > 0 }}'
            - action: samsungtv_smart.art_select_image
              target:
                entity_id: <media_player>
              data:
                content_id: '{{ content_ids | random }}'
                show: true
                # Only the first pick may wake the TV (8.14.1+); later ones
                # are skipped unless Art Mode is already on screen.
                wake: '{{ repeat.first }}'
            - delay:
                minutes: "{{ states('input_select.slideshow_interval') | int(60) }}"
```

Why it is built this way:

- `wake: '{{ repeat.first }}'` keeps the loop from waking the TV. The loop runs
  as long as the toggle is on, also once the TV has been turned off for the
  night, and `art_select_image` powers the TV on and switches it to Art Mode
  by default: one pick per interval used to wake a sleeping Frame every hour,
  and to pull a TV out of the programme being watched. The first pick, right
  after the slideshow is switched on, still wakes it. Needs 8.14.1 — on older
  versions the unknown `wake` key is rejected; wrap the call in an `if` on
  `state_attr('<media_player>', 'art_mode_status') == 'on'` instead.
- A `{% set %}` inside `{% if %}` does not survive the block in Jinja, hence
  the `namespace`.
- The folder sensors publish **absolute paths**, and the file name *is* the
  `content_id` — the integration writes `<content_id>.jpg`.
- Do **not** try to turn `_` back into `:` in those names. The integration
  encodes `:` as `_` when writing the file, so a `_` in a file name is
  undecidable. It does not matter for `SAM-*`, which contain neither.
- `mode: restart` is what makes a **Next** button work: calling
  `automation.trigger` with `skip_condition: true` restarts the sequence, which
  interrupts the `delay` and picks a new artwork immediately.
- The source trigger needs the `conditions` block, otherwise changing the
  source while the slideshow is off would wake the TV.

A **Next** button, for a dashboard:

```yaml
tap_action:
  action: perform-action
  perform_action: automation.trigger
  target:
    entity_id: automation.frame_art_slideshow
  data:
    skip_condition: true
```

> If the selected folder is empty the `condition` stops the run while the toggle
> stays `on`. Add a fallback branch if that bothers you.

---

## Random artwork from favourites and personal photos

A dashboard button cannot evaluate Jinja — a `tap_action` only passes fixed
values. The draw therefore has to live in a script.

```yaml
script:
  frame_art_random_artwork:
    alias: 'Frame Art: random artwork (favourites + personal)'
    icon: mdi:shuffle-variant
    mode: restart
    fields:
      tv_entity:
        name: Frame TV
        required: true
        selector:
          entity:
            domain: media_player
      store_sensor:
        name: Favourites folder sensor
        required: true
        selector:
          entity:
            domain: sensor
      personal_sensor:
        name: Personal folder sensor
        required: true
        selector:
          entity:
            domain: sensor
    sequence:
      - variables:
          content_ids: >
            {% set ns = namespace(ids=[]) %}
            {% set files = (state_attr(store_sensor, 'file_list') | default([], true))
                         + (state_attr(personal_sensor, 'file_list') | default([], true)) %}
            {% for file in files %}
              {% set filename = file.split('/')[-1] if '/' in file else file %}
              {% set content_id = filename.replace('.jpg', '').replace('.JPG', '').replace('.jpeg', '').replace('.JPEG', '').strip() %}
              {% if content_id.startswith('SAM-') or content_id.startswith('MY') %}
                {% set ns.ids = ns.ids + [content_id] %}
              {% endif %}
            {% endfor %}
            {{ ns.ids }}
      - condition: template
        value_template: '{{ content_ids | length > 0 }}'
      - action: samsungtv_smart.art_select_image
        target:
          entity_id: '{{ tv_entity }}'
        data:
          content_id: '{{ content_ids | random }}'
          show: true
```

Call it from a tile. Note `data.variables`, not `data`:

```yaml
- type: tile
  entity: <media_player>
  name: Random artwork
  icon: mdi:shuffle-variant
  hide_state: true
  tap_action: &random
    action: perform-action
    perform_action: script.turn_on
    target:
      entity_id: script.frame_art_random_artwork
    data:
      variables:
        tv_entity: <media_player>
        store_sensor: sensor.<tv>_store
        personal_sensor: sensor.<tv>_personal
  icon_tap_action: *random
```

---

## AI-chosen matte after every artwork change

Whenever the displayed artwork changes — slideshow, gallery, remote, Samsung
app — check whether it has a matte, and if not, let a vision model pick one
from the TV's catalogue.

This uses the [LLM Vision](https://github.com/valentinfrlch/ha-llmvision)
custom integration, because its action takes a **file path**, while
`ai_task` / `openai_conversation` expect a media-source attachment and the
thumbnail lives under `www/`, which is not a media source.

```yaml
script:
  frame_art_auto_matte:
    alias: 'Frame Art: AI matte'
    mode: single
    max_exceeded: silent
    fields:
      tv_entity: {name: Frame TV, required: true, selector: {entity: {domain: media_player}}}
      art_sensor: {name: Frame Art sensor, required: true, selector: {entity: {domain: sensor}}}
      thumbnail: {name: current.jpg path, required: true, selector: {text: }}
      provider: {name: LLM Vision provider id, required: true, selector: {text: }}
      model: {name: Model, required: true, selector: {text: }}
      force: {name: Force, required: false, default: false, selector: {boolean: }}
    sequence:
      - variables:
          content_id: "{{ state_attr(art_sensor, 'current_content_id') }}"
          matte_now: "{{ (state_attr(art_sensor, 'current_matte_id') or 'none') | lower }}"

      # The TV reports "none", "none_none" or nothing at all when there is no
      # matte, and may upper-case the id (SHADOWBOX_POLAR).
      - condition: template
        value_template: >-
          {{ content_id not in [none, '', 'unknown', 'unavailable']
             and (force | default(false) or matte_now.startswith('none')) }}

      - action: llmvision.image_analyzer
        data:
          provider: '{{ provider }}'
          model: '{{ model }}'
          image_file: '{{ thumbnail }}'
          include_filename: false
          target_width: 640
          # The answer is 3 tokens, but a reasoning model counts its thinking
          # against this ceiling: too low and it is cut off before answering.
          max_tokens: 100
          temperature: 0.2
          message: >-
            Pick a matte for this artwork on a Samsung The Frame TV.
            Answer ONLY with a type_colour identifier, lower case, no prose.
            Types: modernthin, modern, modernwide, flexible, shadowbox,
            panoramic, triptych, mix, squares.
            Colours: black, neutral, antique, warm, polar, sand, seafoam, sage,
            burgandy, navy, apricot, byzantine, lavender, redorange, skyblue,
            turquoise.
            The colour must complement the artwork's dominant tone without
            competing with it. Keep the type plain (flexible, modern or
            shadowbox) unless the framing calls for panoramic or triptych.
            Example of a valid answer: flexible_sage
        response_variable: ai

      - variables:
          types: [modernthin, modern, modernwide, flexible, shadowbox, panoramic, triptych, mix, squares]
          colors: [black, neutral, antique, warm, polar, sand, seafoam, sage, burgandy,
                   navy, apricot, byzantine, lavender, redorange, skyblue, turquoise]
          # Never trust the raw answer: extract the first word_word pattern and
          # validate both halves against the catalogue.
          guess: >-
            {{ ((ai.response_text | default('')) | lower
                | regex_findall('[a-z]+_[a-z]+') | first | default('')) }}
          matte_id: >-
            {% set p = guess.split('_') %}
            {% if p | length == 2 and p[0] in types and p[1] in colors %}
              {{ guess }}
            {% else %}
              flexible_neutral
            {% endif %}

      - action: samsungtv_smart.art_change_matte
        target:
          entity_id: '{{ tv_entity }}'
        data:
          content_id: '{{ content_id }}'
          matte_id: '{{ matte_id }}'
        # The service returns {"success": true} or {"error": "..."} instead of
        # raising, so without this a refusal looks like a success in the trace.
        response_variable: matte_result

      - alias: Redisplay the artwork, or the old matte stays on screen
        if:
          - condition: template
            value_template: "{{ is_state(art_sensor, 'on') }}"
        then:
          - delay: {seconds: 1}
          - action: samsungtv_smart.art_select_image
            target:
              entity_id: '{{ tv_entity }}'
            data:
              content_id: '{{ content_id }}'
              show: true

      - action: logbook.log
        data:
          name: Frame Art auto matte
          message: >-
            {{ content_id }}: {{ matte_id }}
            {{ '(AI)' if matte_id == guess
               else '(fallback, unusable answer: ' ~ (ai.response_text | default('empty')) ~ ')' }}
            — TV said {{ matte_result }}
```

Trigger it per TV:

```yaml
automation:
  - alias: 'Frame Art: AI matte'
    mode: single
    max_exceeded: silent
    triggers:
      # Doubles as a debounce and as a settle delay: the integration needs a
      # moment to finish writing current.jpg.
      - trigger: state
        entity_id: sensor.<tv>_frame_art
        attribute: current_content_id
        for: '00:00:10'
    conditions:
      - condition: state
        entity_id: switch.<tv>_art_mode
        state: 'on'
    actions:
      - action: script.turn_on
        target:
          entity_id: script.frame_art_auto_matte
        data:
          variables:
            tv_entity: <media_player>
            art_sensor: sensor.<tv>_frame_art
            thumbnail: /config/www/frame_art/<ENTRY_ID>/current.jpg
            provider: <LLM_VISION_PROVIDER_ID>
            model: gpt-4o-mini
```

**The redisplay step is not optional.** `change_matte` stores the matte but the
panel keeps showing the old frame until the artwork is re-selected. The
integration's own matte selects do this internally; the bare service does not.
The `is_state(art_sensor, 'on')` guard matters too: `show: true` can wake the
panel into Art Mode when nothing is being displayed.

Test the script by hand with `force: true` before enabling the automation —
that bypasses the "already has a matte" check, so it works on whatever is
currently on screen.

If the TV reports `success` and nothing changes, the `type_colour` pair does
not exist on your model: the TV validates and silently ignores it. List what it
really accepts with `samsungtv_smart.art_get_matte_list` and narrow the prompt
to that.

---

## A curated selection, as a folder of symlinks

A hand-picked list of artworks, shown as thumbnails and usable as a slideshow
source. The trick: **the selection is a real directory of symlinks**, so a core
`folder` sensor exposes it with a `file_list` attribute in exactly the format
`folder-gallery-card` and the slideshow already consume.

### configuration.yaml

```yaml
homeassistant:
  # Required: the `folder` integration refuses any directory outside this list.
  allowlist_external_dirs:
    - /config/www/frame_art

shell_command:
  # Home Assistant does NOT run these through a shell: it splits the line and
  # execs the binary. No redirections, no pipes — plain executables with
  # arguments only.
  art_selection_mkdir: mkdir -p {{ selection_dir }}
  art_selection_add: >-
    ln -sfn /config/www/frame_art/{{ entry_id }}/{{ subdir }}/{{ content_id }}.jpg
    {{ selection_dir }}/{{ content_id }}.jpg
  art_selection_remove: rm -f {{ selection_dir }}/{{ content_id }}.jpg
  # A symlink whose target is deleted breaks the folder sensor: it sums
  # os.path.getsize() over every file, which raises on a missing target and
  # fails the whole update. -L is what makes find see a broken link as a link.
  art_selection_prune: find -L {{ selection_dir }} -maxdepth 1 -type l -delete

sensor:
  # The last path component becomes the sensor name, so it MUST be unique
  # across entries — see the pitfalls section.
  - platform: folder
    folder: /config/www/frame_art/<ENTRY_ID>/selection_<tv>
    filter: '*.jpg'
```

Create the directory **before** restarting: the `folder` platform only creates
its entity if the directory exists at startup.

### The toggle script

```yaml
script:
  frame_art_selection_toggle:
    alias: 'Frame Art: add to / remove from my selection'
    icon: mdi:playlist-star
    mode: queued
    max: 25
    fields:
      content_id: {name: Content ID, required: true, selector: {text: }}
      entry_id: {name: Entry ID, required: true, selector: {text: }}
      selection_dir: {name: Selection directory, required: true, selector: {text: }}
      selection_sensor: {name: Selection folder sensor, required: true, selector: {entity: {domain: sensor}}}
    sequence:
      - variables:
          files: "{{ state_attr(selection_sensor, 'file_list') | default([], true) }}"
          # Compare the full file name, suffix included: without '.jpg',
          # SAM-S2701 would also match SAM-S27011.
          already: "{{ ('/' ~ content_id ~ '.jpg') in (files | join(' ')) }}"
          # Same classification the integration uses when it files thumbnails.
          subdir: >-
            {{ 'store' if content_id.startswith('SAM-')
               else ('personal' if content_id.startswith('MY') else 'other') }}

      - if:
          - condition: template
            value_template: >-
              {{ selection_dir is not string or not selection_dir.startswith('/config/') }}
        then:
          - stop: "invalid selection_dir: {{ selection_dir | default('(missing)') }}"
            error: true

      - if:
          - condition: template
            value_template: '{{ already }}'
        then:
          - action: shell_command.art_selection_remove
            data: {selection_dir: '{{ selection_dir }}', content_id: '{{ content_id }}'}
            response_variable: sh
        else:
          - action: shell_command.art_selection_mkdir
            data: {selection_dir: '{{ selection_dir }}'}
            response_variable: sh_mkdir
          - if:
              - condition: template
                value_template: '{{ (sh_mkdir.returncode | default(1)) != 0 }}'
            then:
              - stop: "mkdir failed ({{ sh_mkdir.returncode }}): {{ sh_mkdir.stderr }}"
                error: true
          - action: shell_command.art_selection_add
            data:
              selection_dir: '{{ selection_dir }}'
              entry_id: '{{ entry_id }}'
              subdir: '{{ subdir }}'
              content_id: '{{ content_id }}'
            response_variable: sh

      - action: shell_command.art_selection_prune
        data: {selection_dir: '{{ selection_dir }}'}

      # The folder sensor only polls: force a read so the gallery updates now.
      - action: homeassistant.update_entity
        target:
          entity_id: '{{ selection_sensor }}'

      - action: logbook.log
        data:
          name: Frame Art selection
          message: >-
            {{ content_id }} {{ 'removed from' if already else 'added to' }} the selection
            — code {{ sh.returncode | default('?') }}
            {{ '· stderr: ' ~ sh.stderr if (sh.stderr | default('')) else '' }}
```

`selection_dir` is passed explicitly rather than read from the sensor's `path`
attribute on purpose: on the very first press the directory does not exist, so
neither does the sensor, and there would be no path to read.

### Wiring it to the galleries

`hold_action` on the main gallery adds, `hold_action` on the selection gallery
removes. Long-press was free because it duplicated double-tap in the default
config. The card substitutes `{{content_id}}` recursively, including inside
`data.variables`.

```yaml
- type: custom:folder-gallery-card
  folders:
    - {name: Personal, folder_sensor: sensor.<tv>_personal}
    - {name: Favourites, folder_sensor: sensor.<tv>_store}
  frame_tv_entity: <media_player>
  tap_action: lightbox
  double_tap_action:
    perform_action: samsungtv_smart.art_select_image
    target: {entity_id: <media_player>}
    data: {content_id: '{{content_id}}'}
  hold_action:
    perform_action: script.turn_on
    target: {entity_id: script.frame_art_selection_toggle}
    data:
      variables:
        content_id: '{{content_id}}'
        entry_id: <ENTRY_ID>
        selection_dir: /config/www/frame_art/<ENTRY_ID>/selection_<tv>
        selection_sensor: sensor.selection_<tv>
```

Then a second gallery card with `folder_sensor: sensor.selection_<tv>` shows
the selection, and `Selection` becomes a slideshow source.

Symlinks over copies: no duplication on disk, and a thumbnail that gets
re-downloaded updates the selection view for free. The cost is the broken-link
case, which `art_selection_prune` handles.

---

## Platform pitfalls

Everything below cost a debugging round while building these recipes.

### Home Assistant

- **A `tile` card requires an `entity`.** Unlike `button`, it renders
  *Configuration error* without one. For an action-only tile, give it any
  sensible entity, add `hide_state: true`, and set `icon_tap_action` to the
  same action as `tap_action` — otherwise tapping the icon keeps the domain's
  default action (on an `input_boolean` "Stop" tile, that is a *toggle*, which
  turns the thing back on).
- **A script's service name is its `object_id`, not its entity id.** The UI
  assigns a numeric key, so a script created in the UI registers
  `script.1730000000000` even after you rename its entity id. Either define it
  in `scripts.yaml` with the key you want, or always call it as
  `script.turn_on` targeting the entity, passing parameters under
  `data.variables`.
- **`shell_command` has no shell.** The rendered line is split and exec'd, so
  `>`, `|` and `&&` do not work. It also never raises on a non-zero exit:
  capture `response_variable` and check `returncode` yourself.
- **The `folder` platform names its sensor after the last path component**, and
  its entities have no `unique_id` — they never enter the entity registry and
  **cannot be renamed**. Two directories called `selection` give you
  `sensor.selection` and `sensor.selection_2`, with the suffix depending on
  startup order. Give each directory a distinct name instead.
- **A `folder` sensor is only created if the directory exists at startup**, and
  the directory must be inside `allowlist_external_dirs`.
- **`rstrip` is not a Jinja filter**, it is a string method:
  `{{ (x | string).rstrip('/') }}`, not `{{ x | rstrip('/') }}`.
- **A `{% set %}` inside `{% if %}` does not survive the block.** Use
  `{% set ns = namespace(...) %}`.
- **A YAML folded scalar (`>`) turns newlines into spaces.** Worth knowing when
  you search-and-replace inside a template stored that way.

### custom:button-card

- **`template:` is a top-level key, not a `state[]` key.** Inside an entry of
  the `state:` list the valid keys are `operator`, `value`, `styles`, `icon`,
  `color`, `name`, `label`, `entity_picture`, `spin`, `id`. A `template:` there
  is silently ignored, so the condition evaluates and no style is applied.
  Put the `styles:` block inline in each state entry.

### This integration

- **`number.<tv>_art_mode_brightness` and `_art_mode_color_temperature` are
  `unavailable` unless art is actually on screen.** Outside Art Mode the TV
  either refuses the read or answers with defaults, so the entities go
  unavailable on purpose. The TV scale is 1–10, hence the 10% step.
- **`art_change_matte` does not refresh the panel.** Re-select the artwork with
  `show: true` after a ~1 s settle, and only while art is displaying.
- **The art services return `{"error": ...}` instead of raising.** Use
  `response_variable` if you want to know whether the TV accepted anything.
- **An unsupported `matte_id` is a silent no-op.** The 10 types × 16 colours
  listed here are the integration's fallback catalogue; your model may accept
  fewer. Ask it with `art_get_matte_list`.
- **Thumbnail layout.** The displayed artwork is written to
  `www/frame_art/<ENTRY_ID>/current.jpg` and overwritten in place; per-artwork
  thumbnails go to `<ENTRY_ID>/{store,personal,other}/<content_id>.jpg`, with
  `SAM-*` in `store`, `MY_F*` in `personal`. No `camera` platform is provided —
  make a *Local File* camera on `current.jpg` if you want the current artwork
  as a camera entity.

---

## See Also

- **[Frame Art Guide](Frame_Art.md)** - Complete service documentation
- **[Frame Art Gallery Guide](Frame_Art_Gallery.md)** - Interactive Lovelace galleries
