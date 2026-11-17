# Changelog

## 0.13.0 (unreleased)

- **Real light types: Area, Spot, Point and Sun.** A light's *Type* is now
  Blender's own light, with what that light can do: an area light's shape
  (Softbox, Strip, Disc, Oval), width, height and spread; a spot's beam,
  edge softness and bulb size; a point light's bulb size; the sun's size.
  Before, every Plinth light was an area light and "Spot" only narrowed its
  spread. Switching type keeps how bright and soft the light looks; looks
  remember each light's type and a spot's beam; gobos work on every type.
- **See your lights and shots.** The Light step draws every light as what it
  is (the panel in its shape, a spot's cone out to its target, a bulb, a
  sun) with a faint line to what it aims at; the Camera step draws every
  shot's frame. The one you're editing is red.
- **Fixed: glass behind glass turned grey in Eevee.** A model's own glass
  (windows, headlight lenses) used Eevee's refraction, which sees only one
  layer: glass close behind glass blurred into a grey sheet. Thin, clear
  glass now gets the same see-through pane Plinth's own glass uses in Eevee,
  so every layer shows, sharp; Cycles still renders the material as it is.
  Frosted and solid glass keep refracting.
- **Fixed: the Studio card's Backdrop slider stuck at 0.** Dragged to black,
  it could never be brought back up; it now comes back with the colour it
  had. The edit pages' Hue and Saturation strips also keep the colour
  through black and grey instead of turning it grey.
- **Background colour.** *Edit studio › Colour* now edits the Backdrop or the
  Background: the colour seen past the backdrop (an endless floor's horizon,
  Infinity white's surround), which also tints reflections. A look that
  doesn't set it leaves yours alone; Reset puts the look's back.
- **Fixed: removing the studio's lights** (in the outliner, or one by one)
  made the Light step ask you to build a studio. It now edits whichever light
  is left, and with none left offers *Add a light* (any shape) and *Bring back
  the lights* (Key, Rim and Fill with the look's balance); the + and HDRI chips
  stay.
- **Exact target placement.** *Edit light* and *Edit shot* have a Target tab:
  which target it aims at, X / Y / Z sliders (type a value for exact
  metres) and *Centre on product*. Dragging the icon still slides it over
  surfaces.
- **Mapping: triplanar or UV, flip and turn.** *Edit material › Mapping* on
  every patterned material (carbon, leather, fabric, wood, stone, metal,
  paint flake, UDIN carbon and forged carbon...): project from three sides
  (no UVs needed) or follow the part's UVs, at a UV size fitted to the part;
  Flip U, Flip V and Turn. Mapping is per object, so the left and right halves
  of a bonnet can share one carbon material and weave in opposite
  directions, meeting in a mirrored seam.
- **Vantablack** (Trim): a total blackout material that reflects nothing,
  for gaps, grilles and interiors.
- **Texture sets: the textures your model came with.** A new *Texture set*
  material (Image family) takes colour, opacity, metal, roughness (or gloss),
  normal (OpenGL or DirectX), height, occlusion, glow and packed ORM/ARM maps.
  Find them four ways: automatically (the imported material's images, broken
  paths searched for next to the file and in textures/maps folders, and the
  rest of the set beside them), from a folder (the set whose name matches the
  part), from images already in the file, or one map at a time by hand.
  File names are read with Node Wrangler's tags (BaseColor, Roughness, nor_gl,
  ...). Material ⋯ › *Find its textures* / *Textures from a folder…*.
- **Hide a part to reach what's under it.** H (or Material ⋯ › *Hide this
  part*) hides the selected part, or the one under the mouse; H again, or the
  *Show* pill, brings back exactly those. Shift+H hides more. Renders still see
  them, and it never undoes `/` isolate.
- **Open source, fully offline.** Plinth is now developed in the open on
  GitHub. The account, sign-in, plans and online updates are gone, and
  Plinth no longer asks Blender for network access. Content packs still
  install from a .zip (Preferences › Library › Install from file…).
  Preferences › Help has *Copy support info* for bug reports, and links to
  GitHub Issues, Discord and nosferatu.cc.

## 0.12.1 (2026-11-14)

- **Fixed: Blender could crash a few seconds after starting** on some
  computers (reported on a GTX 1650 + Intel UHD laptop). Plinth no longer
  lists Cycles' render devices on its own at start, which loaded every GPU
  runtime (CUDA, OptiX, HIP, oneAPI) into Blender; it now reads your saved
  preferences and only probes when you click *Use the GPU for Cycles*.
- Graphics cards with 4 GB or less (GTX 1650/1050, MX, RTX 3050 Laptop...)
  now get the Light performance tier instead of Balanced.

## 0.12.0 (2026-11-11)

Scenes rethought from reference, with real scanned maps everywhere
(testers: "the ground resorts to colour ramps", "the trees are one plain
colour, no leaves", "they need to be more in-depth, more lifelike").

- **Scanned maps built in**: Plinth now ships ~40 scanned texture sets at 2K
  (Poly Haven, CC0), five leaf atlases (ambientCG, CC0) and NASA's Earth
  (Blue Marble, Black Marble; public domain), about 85 MB. No scene falls
  back to colour ramps: every ground is layered scanned maps. Scene kits carry
  4K copies of each scene's hero maps (and a 4K Earth), used at Ultra.
- **Grounds**: several scanned sets per ground, each at its real size, never
  visibly repeating (a turned second sample, a far third), stacked by height
  blending (moss climbs out of the litter, pebbles show through the mud), with
  macro variation, darkened crevices, wetness (porous darkening, gloss,
  puddles that flatten the relief) and real displacement at High/Ultra.
- **Moon**: an Apollo site: fresh craters with bright rayed ejecta and rock
  fields, worn craters at every scale, rounded highland massifs, a rover's
  twin tracks curving past and bootprints walking up to the product; Earth
  with its real oceans (glinting), cloud bands, city lights on the night
  side and a blue atmospheric limb; its phase follows the Sun.
- **Forest**: beech and spruce woodland grown in code with real separate
  leaves (thousands of scanned leaf cards a tree; needle sprays on spruces),
  crowns spaced (never inside each other), stands of each species, young
  trees in the gaps, saplings in clumps; a meandering stream with a pebble
  bed, wet banks and flowing water; ferns along it, mossy boulders, a trunk
  fallen across it.
- **Desert dunes**: the Namib: asymmetric dunes with sharp crests, rippled
  windward backs and smooth slipfaces, gravel flats, a white clay pan with
  dead camelthorn trees, dry grass tufts.
- **Salt flats**: Uyuni: raised white salt polygons, tyre ruts, rows of salt
  cones, hazy mountains; After rain turns the flat into the sky's mirror.
- **Modern house**: scanned stucco, timber siding, a stacked-stone wall and
  big-format paving; a furnished room behind the glass; box balls, hedges,
  ornamental grass, a Japanese maple and a woodland belt; mower stripes.
- **Abandoned lot**: layered broken asphalt with faded bays, oil stains and
  rain puddles; oil drums, tyres, pallets and crates; a derelict warehouse.
- **Scene settings** (Edit studio > Scene: Place / Look / Items):
  **Variation** (Shuffle: another random layout; Original), **Time of day**
  (Dawn, Day, Golden hour, Dusk, Night: the sun, the sky, stars and
  moonlight, lamps and lit rooms switching on), **Weather** (Clear,
  Overcast, After rain, Mist: clouds, soft light, wet ground and puddles,
  aerial perspective), **Wind** (plants and trees sway in turntables and
  animations; water flows), and per category (Trees, Plants, Rocks, Props)
  **Amount**, **Size** and **Spread** plus a **Clearing** round the product:
  all live, without rebuilding.
- Aerial perspective: every scene material fades into the haze with distance.
- Plinth's shot cameras reach the whole place (their clip end grows while a
  scene is on and comes back after); the studio view sees 5 km.

## 0.11.0

- **Scene detail**: Studio › Detail ▾ (and Edit studio) builds a scene at Low,
  Medium, High or Ultra, for the viewport and the render alike. The ground is
  a polar grid dense where the camera looks (from ~30 thousand vertices at Low
  to ~1.3 million at Ultra) with real displacement from its height map at High
  and Ultra; how much is scattered and which level of detail scanned assets
  come in follow the level. A lower level is the same place, thinner (it draws
  a prefix of the same points). Changing it rebuilds in place and keeps Turn.
- **Instanced scatter**: trees, rocks, plants, grass and props are Geometry
  Nodes instances of one copy each, so a forest of hundreds of scanned pines
  or tens of thousands of grass tufts stays light in memory.
- **Scene kits**: an optional download per scene from the content library,
  of scanned CC0 assets and textures from Poly Haven (Moon: moon rocks and
  regolith; Desert: stones, dry branches, sand; Salt flats: cracked crust and
  mountain rock; Neon: wet asphalt; Modern house: shrubs, planters, a street
  lamp, terrace furniture, firs, paving, render and timber; Forest: firs,
  pines, saplings, ferns, nettles, grass, mossy rocks, stumps, fallen trunks
  and roots; Abandoned lot: road barriers, barrels, tyres, bins, crates, weeds,
  broken asphalt, rust and brick). A scene shows its made-in-Plinth version at
  once and rebuilds with the kit when it's in. Without a kit, scenes are also
  more detailed than in 0.10: chiselled rocks, frond conifers, grass tufts,
  wind ripples, small worn craters, ridged mountains with haze.
- **Install from file**: Preferences › Library › Install from file… installs
  any Plinth content pack from a .zip on this computer (checked like a
  download).
- Scene materials follow the ground's UVs and object space, so turning a
  scene no longer slides its patterns under it.
- **Scene materials and light, rebuilt** (testers: "flat, no depth, no
  normals, no specular, no emission"):
  - Grounds have depth up to the product's footprint (the displacement was
    masked off 2-3 r out, ~7 m for a car), a stronger normal map, a finer
    bump from the height map, crevices darkened from it, roughness worn
    smoother on high points.
  - Every code-made surface is layered: broad and fine colour, pits that
    darken in their hollows, roughness that varies, relief at two real sizes;
    nothing a scene makes is left with a constant roughness or no normal.
  - Physically based specular: scanned materials that shipped with almost no
    reflection (Specular 0-0.02) reflect like any dielectric (0.5, ~4%).
  - Lower, stronger suns (golden hour at the house, raking light on the
    dunes), less studio fill, and the studio lights gone from reflections and
    glass outdoors (a softbox in a puddle gave the studio away); Studio puts
    them back exactly.
  - Things that glow: the house's lit room behind real architectural glass,
    an LED soffit, lit bollards; the Abandoned lot at dusk with sodium lamps
    lighting the ground; a hotter Neon grid with a soft halo on a wet floor;
    the Earth's oceans glint.
  - Clouds that drift (with the frame: turntables show them move) over the
    clear-sky scenes, and dark ground below the horizon in reflections.
  - Forest floor litter (scanned bark and branches) and more undergrowth.

## 0.10.0 (in progress)

From testers' feedback:

- **The shot bar lives with the shots**: it shows in the Camera and Shoot
  steps (and anywhere while a batch renders), not over the Studio, Light and
  Material steps.
- **/ isolates the product, not the studio**: only the selected part(s) stay,
  with the backdrop, lights and shots still there (Blender's local view hid
  them). / again shows everything; renders are unchanged.
- **M + click copies a material, M + right-click pastes it** onto any other
  part with all its settings (its own copy). In the Plinth workspace M no
  longer opens Move to Collection. A mesh with no material yet can be picked
  and pasted onto.
- **Perspective or orthographic shots**, and **Look from Front, Side, Rear,
  Top** in one click (Camera card and Edit shot › View). Orthographic keeps the
  product the same size; its lens becomes **Size**.
- **Clear sky** in the HDRI choices: a physical sky with a sun you can move
  (Sun height, Sun direction, Sun softness, Haze) and drag in the viewport. The
  sun is a lamp in line with the sky, its strength and colour measured against
  Cycles' own sky sun at every height, because Eevee doesn't render the sky's
  sun disc (Eevee came out at 0.13 of Cycles without it, ~0.85 with it, like the
  studio lights). Looks and content packs keep the sun.
- **Scenes**: Studio › Scene ▾ puts the product in a place instead of the
  studio. Procedural (made in Plinth): **Moon** (craters, rocks, a starry sky
  and the Earth lit as a crescent by the same sun), **Desert dunes**, **Salt
  flats**, **Neon grid**. 3D sets: **Modern house** (glass, concrete, timber
  slats, its lights on), **Forest** (a clearing among hundreds of pines, rocks,
  logs and grass), **Abandoned lot** (cracked asphalt with puddles, barriers,
  rusty containers, a broken wall). Photo scenes from the new *Real locations*
  content pack (Poly Haven, CC0): old car park, overpass, forest road, modern
  city, suburb street, desert road. **Turn** spins the place; Studio puts
  everything back. Photo grounds are projected from the photo with their
  albedo divided by the light the photo throws on a floor, so lit by the photo
  they match it (measured within 12% in Cycles and Eevee; Eevee's indirect
  boost is divided back out by a driver) and catch the product's shadow; a
  bowl and dome hold the rest of the photo upright and grow to hold every shot.
- **Decals carry on across panels**: a decal near a shut line continues over
  the touching panels in the same material (Across panels, on by default).
  Fixed on the way: parts sharing one linked material lost each other's decals
  when one of them was rebuilt.

## 0.9.1 (2026-11-10)

- **The material browser no longer freezes Blender on big products.** With a
  car of a few hundred parts open, the browser re-read every face of every
  part on each redraw to list what's *In this scene* (about 2 seconds a
  frame), so Blender stopped responding and Windows closed it. Parts are now
  measured once and remembered until their faces change; a redraw on the
  same car takes a few milliseconds, and previews no longer give up while it
  was busy.
- **Blender 4.2 LTS works again.** The bundled UDIN materials (car paints,
  carbon, chrome, plastic, glass) were saved in Blender 5's file format,
  which 4.2 can't open, so they all failed there. They're now built with 4.2,
  which every supported Blender reads. Decals on a part whose material has no
  nodes work on 4.2 too.
- Three Eevee settings 4.2 doesn't have (fast GI switch, indirect light
  boost, anisotropic filtering, which 4.2 keeps in Preferences) are skipped
  there by design.

## 0.9.0 (2026-11-10)

- **Glass, rebuilt on the UDIN Windshield.** Every glass preset is now one
  glass type with a separate look per engine. Cycles renders real glass, with
  see-through shadows so interiors aren't black. In Eevee, windows and other
  panes no longer refract, so there's no magnification, the view through is
  sharp, the tint shows, and glass behind glass stays visible (Eevee could only
  draw one layer of refracting glass, so a windshield seen through a side
  window used to vanish). Solid glass (Crystal) and frosted glass still
  refract in Eevee.
- New glass presets: Bronze tint, Smoked lens, Tail-light red, Acrylic,
  Crystal. Glass gains UDIN's wear controls (imperfections, smudges,
  scratches, stains).
- Files from 0.7–0.8 update themselves when opened: automotive glass parts
  become the new glass with the same tint and wear.
- **Eevee looks much closer to Cycles.** Against a Cycles Final render of the
  Car studio, Eevee's per-pixel difference is half what it was (0.107 → 0.049).
  Eevee's default soft shadows over-darkened big lights grazing the cove, so the
  Car look's floor was a third as bright as in Cycles. Plinth lights now use
  jittered (physically based) soft shadows, and the viewport shows them in the
  Studio, Gloss and Final presets.
- Sharper Eevee: reflections and refraction are traced at full resolution with
  full screen-trace quality, the pixel filter is 1.15 px, textures get 16×
  anisotropic filtering, and overscan keeps reflections from fading at the
  frame edge.
- **Eevee Final**, a new step on the Shoot quality scale between Eevee Studio
  and Cycles Preview: Eevee at its best, as sharp as Cycles in our measurements.
- **Cycles presets** use adaptive sampling (samples are now a ceiling), the
  light tree, OpenImageDenoise at its best quality with albedo and normal, and
  enough transparent bounces for layered glass. Hero adds refractive caustics
  and, on the CPU, path guiding. Batch shoots keep Cycles' scene data between
  shots, so only the first shot rebuilds it.
- **Colour look** (Shoot › Edit output, and per studio look) is now True
  (Khronos PBR Neutral: colours as you set them), Natural (AgX), Punchy or
  Standard, and the viewport shows it live. Clean white and Soft daylight use
  True, Car studio Natural, Dark luxe Punchy.
- **Performance** (Preferences): Auto, Light, Balanced or Max fits Eevee's shadow
  memory and probe and ray-tracing resolution to your graphics card.
- **Material browser.** "Browse materials…" opens a panel beside the Material
  card with a search box (type anywhere on it; "alcantara", "windshield" and
  "pea glow" all find what you mean), Favourites, Recent, In this scene, My
  library (with folders) and families with counts: Paint, Metal, Glass,
  Plastic & trim, Carbon, Fabric & leather, Rubber & tyres, Brakes, Glow &
  screens, Image. You get live 3D previews in a grid or list, and the scroll
  wheel scrolls. Right-click a material to apply it, favourite it, or (your
  own) rename, duplicate, update it from the part, move it to a folder or
  delete it.
- **Many parts:** "Apply to: This part · Parts like it · Selected". Parts like
  it (e.g. four wheels that shared "Tyre") get one linked material, so an
  edit changes them all; ⋯ › Unlink gives one part its own copy again.
- **The Material card** shows what the part wears (click to browse), your
  three most recent materials, and **Save** as a visible button. ⋯ › Card
  sliders… picks which controls the card shows (up to four per type).
- **Your own materials are editable.** Imported or hand-made materials get an
  Edit page (Colour, Surface, More) from their shader's inputs, edited in place.
- Saved materials can be renamed, updated, duplicated and filed in folders;
  previews are kept on disk, so the browser opens with them ready.
- Preferences › Material picker: Browser (new) or Compact (families in a ▾
  and their materials on the card).
- **New materials.** Fabric & leather: Alcantara, Suede, Velvet, Woven
  fabric, Canvas, Knit, Open mesh (see-through), Nappa, Perforated leather,
  Saddle tan. Wood & stone: Walnut and Oak (gloss lacquer or oiled open-pore),
  Grey ash, Concrete, Satin concrete, White and Black marble, Glazed ceramic,
  Stoneware, Cardboard. Metal: Satin chrome, Anodised blue, red and black,
  Satin titanium, Brass, Brushed gold, Cast aluminium. Plastic & trim:
  Soft-touch, Satin plastic, Rubber seal, Satin acrylic. All are real-size
  patterns that hold up close.
- **Tyre sidewalls**: Tyre sidewall, Shiny sidewall and Dusty sidewall for a
  sidewall that is its own part, and Whitewall for fitted tyres (a white band
  you can widen). Decals gain **Raised**, which embosses a decal's shape into
  the surface: moulded sidewall lettering, embossed badges.
- **Studio looks are the whole studio**: a look can set the floor's
  reflection and roughness, the cove's shape, the world colour or an HDRI,
  where Key, Rim and Fill sit and their shape, extra lights, the colour look
  and the Eevee preset (when Auto preset is off). The next look puts back
  anything the last one changed that it doesn't set itself; what you changed
  yourself stays. Saved looks keep all of it, and looks saved in 0.8 still
  load.
- **Ten new looks**: Infinity white, Black gloss (car reveal on a mirror
  floor), Showroom (overhead strip panels), Gradient grey, Golden hour (with a
  sunset HDRI), Neon edge, Tabletop warm, Soft pastel, Tech dark, Overcast sky
  (outdoor HDRI). The Studio card shows five, and **All looks…** lists every
  one.
- **Studio shapes**: the backdrop itself is now one of eight live meshes,
  picked from **Shape ▾** on the Studio card: Sweep (the classic cove),
  Corner cyc, Three-wall, Half-round, Slanted room (four walls with 45°
  corners), Round cyc, Dome (closed roof, no edges anywhere) and Endless
  floor. Curve and Wall height work on every shape; Edit studio adds Width,
  Depth and Corner, and a **Walls** tab for rooms: each wall on or off,
  slanted or rounded corners, and a dome roof. Shapes follow the product's
  size, survive Reset and Build Studio, and every shot sees only studio.
- **Six looks built on shapes**: Dome studio, Corner studio, Round cyc,
  Slanted room, Half-round and Horizon (an endless floor under an open sky).
  Saved looks keep their shape.
- Search knows more words: microsuede, upholstery, veneer, porcelain, cement,
  kraft, sidewall, whitewall, anodized.
- **Cycles on the GPU in one click**: if your graphics card isn't set up for
  Cycles (it renders on the CPU until it is), Shoot › Edit output › Engine and
  Preferences offer to switch it on.

## 0.8.0 (in progress)

- **Plinth account** (optional): sign in from Plinth's preferences or the
  Studio card. Plinth opens the website with a code (PLN-XXXX); approve it
  there and Plinth is signed in. Signing in unlocks updates and the content
  library; Plinth itself works fully whether you're signed in or not.
- One computer at a time: signing in on another computer signs this one out
  ("Signed in on another computer"); everything installed keeps working.
- Works offline: your plan is checked when Plinth can reach the server and
  stays valid for 30 days between checks.
- Respects Blender's "Allow Online Access": with it off, Plinth never
  connects and says so, with a button to the setting.
- **Updates in Blender's Extensions screen**: signing in adds your account's
  Plinth repository to Blender, so new versions show up as normal updates.
  "Update available — vX.Y" appears in Plinth's preferences and status line
  (click it to open Extensions). A copy installed from disk can switch to
  account updates with one click (Preferences › Account).
- **Content library**: new studio looks, light rigs, material presets and
  HDRIs arrive without a Plinth update. They appear where the built-in ones
  are (Look grid, a new Rig row on the Light card, the Material card and its
  Edit page, the HDRI chips), with a small dot until you use them, and the
  status line says what's new ("3 new materials"). Preferences › Library
  lists your packs (size, version, Remove, Re-download), sets the folder, and
  chooses between "Download everything" and "Only download when I use it".
  Everything you've downloaded keeps working offline and after a subscription
  ends.
- **Copy support info** (Preferences › Account): your Plinth and Blender
  versions, system, plan and content packs, ready to paste into a support
  email. No passwords or tokens are included.

## 0.7.0 (in progress)

- Panels have no border: the soft grey hairline around every black surface is
  gone (solid black only). Dividers inside cards are crisp 1 px lines; outline
  buttons and rings snap to whole pixels, so they stay sharp at any UI scale.
- Menus open clear of the shot bar instead of touching it.
- **Studio navigation** (Plinth workspace only): left-drag orbits, middle-drag
  pans, right-drag zooms (up = closer), double-click sets the orbit centre.
  Looking through a shot the drags move the shot (around, higher/lower,
  closer, reframe with shift; Lens never changes), the Camera card follows
  live, one Ctrl+Z undoes a drag, and double-click gives the shot its own
  pivot ("Hero pivot"). Trackpad swipe/pinch and the wheel too. A click still
  selects; right-click opens no menu; Esc or a right-click mid-drag puts
  things back. A first-run hint explains it; the status line says
  "DRAG MOVES HERO" in a shot. Everywhere else Blender's mouse is unchanged.
  Preferences: Mouse in the studio (Plinth / Blender), Orbit speed, Invert
  zoom.
- **The studio remembers your view.** Changing step no longer re-fits the
  view every time: the product is framed the first time, then each step comes
  back the way you left it (orbiting, or looking through the shot) and the
  orbit view is the one you last had. Building the studio again starts over.
- **Live 3D material previews.** Every preset tile shows the preset rendered
  on a sphere by Blender's preview renderer, in your part's colour and with
  your edits for that preset; the current preset re-renders as you drag its
  sliders. Previews are never saved in the file.
- **Tyre** materials (new Material type): Road, Performance (directional V),
  Winter (siped), All-terrain, Racing slick and Worn, plus plain, dusty and
  shiny rubber. Tread on the running surface and sidewall on the sides, fitted
  to each tyre's own shape (four tyres in one mesh work) and turning with the
  wheel. Tread depth, blocks, grooves, groove width, direction, sipes, wear,
  sidewall shine and ribs, dust.
- **Image** materials (new Material type) for pictures made for a part:
  instrument clusters, screens, labels, printed panels. Choose image… on the
  card; size, turn, offset, brightness, glow, gloss. Presets: Printed, Gloss
  print, Screen, Label.
- **Decals and liveries** (Material card ⋯ › Add decal… / Add livery (UV)…):
  logos, numbers and stickers projected onto any part (no UVs needed), dragged
  over the product by their handle, with size, turn, opacity and gloss; and
  full livery images on the part's UVs. Any number per part, over any finish,
  kept through preset changes and Original (your own material is never edited:
  the part gets a Plinth copy).
- **Decals are a button now**: the Material card has **Add decal…** and
  **Add livery…** under the sliders (with how many the part has), for every
  finish. The ⋯ menu still lists them.
- **Eight new gobos**: Spot (one pool of light), Water (caustics), Crackle,
  Chequer, Ripples, Lattice (a diamond trellis), Breakup (soft cloud
  patches) and Grille. The Gobo tab shows all 14 four across.
- **Brake** materials (new Material type): Plain disc, Cross-drilled,
  Slotted, Drilled + slot, Carbon ceramic, Race heat and Weathered discs, and
  a painted Caliper. Discs are fitted to the part's shape like tyres (four in
  one mesh work, they turn with the wheel): drilled holes in a spiral,
  curved slots, vents on the edge, turning rings, a hat in its own colour,
  heat tint, flash rust and carbon-ceramic speckle. Hat size, Heat tint and
  Rust are on the card.
- **Camera dots**: in the Camera step, orbiting, every shot shows as a dot
  with its name, like the lights; click one to make it the active shot.
- **Part row**: a mesh with several materials gets a **Part** row at the top of
  the Material card, one chip per material its faces show (five, then
  "All N ▾"), so you pick which one Plinth overrides.
- **My materials** (Material › Type › **Saved**): save a part's material
  (**Save this part**, or ⋯ › Save to my materials) and put it on any part,
  in any file, with one click. Plinth materials are saved as recipes (preset
  + every setting, live 3D preview); your own materials are saved too and
  appended once per file. Double-click a tile to rename it; **Remove** takes
  it out of the library (parts using it keep it). **Original** still brings
  back what the part had.
- **Multi-material meshes**: changes now always land on a part you can see.
  Plinth edits the active material slot, and on imported meshes that was
  often a slot no face uses (or one a Geometry Nodes Set Material overrides),
  so nothing changed on screen. Now Plinth measures which slots the faces
  really show (modifiers included) and keeps the active one on them;
  **clicking the product in the Material step picks the part under the
  mouse**; and the part list (title ▾) shows only parts on the mesh.
- **Clear glass is clear in Eevee again.** Eevee counts what it sees through
  glass as indirect light, and the Studio/Gloss/Motion presets boosted that
  1.5x, so panes looked frosted (a milky veil). The boost is 1.25x now: glass
  keeps the view behind it, and the Eevee preview is also closer to Cycles
  overall (worst histogram distance 0.39 -> 0.32).
- Fixed: entering the Camera step (wheel or trackpad over a shot) raised
  "'Event' object has no attribute 'is_direction_inverted'" on Blender 5.2.
- The floating "Paint highlight · then click the product · Shift moves the
  light" line is gone from the Light step; Paint Light stays in the sidebar (N).
- **Move · Rotate · Scale** (top left, one at a time): Blender's own transform gizmos. In
  Light they move the light you're editing, in Camera the active shot, and
  elsewhere whatever is selected (the product, the backdrop, anything you
  click). Moving keeps a light or shot aimed at its target; turning one frees
  its aim (Aim at puts it back). The product selection comes back when you
  leave the step or switch the gizmos off.
- Material › Type is two lines now (eight types). Three preset names were cut
  on the card and are shorter: Brushed alloy, Fine carbon, Textured trim.
- **Glow** materials (new Material type): LED, LED light bar (real-size LED
  dots), Tail light, Indicator, Neon, Screen, Light panel. Brightness, edge
  fade, LED dots, and the surface/lens under the glow.
- **Bake lighting** for Eevee (Shoot › Engine, or the engine menu): a light
  volume fitted to the studio, baked in about a second, giving Eevee bounce
  light, backdrop colour bleed and light from Glow parts. The button says when
  the lighting changed since the bake. Your own light volumes are left alone.
- **Bake materials** (Material card ⋯ › Bake textures · 1K/2K/4K): a part's
  procedural material becomes image textures (base colour, metallic,
  roughness, coat, glow, normal), quick in Eevee and the same look. The part
  gets its own UV map; your UVs and other parts are untouched. "Back to
  procedural" (or picking any preset) restores the editable material. Glass
  stays procedural.

## 0.6.0

- Gobos work in Eevee: a pattern is now a card in front of the light that only
  casts shadow (light-node patterns were Cycles-only, so Eevee showed a plain
  light). Same result in both engines, live in the viewport. A light with a
  pattern becomes a small projector so the pattern stays crisp; Edge softness
  sets its size, and None gives the light its own size back. Cards follow the
  light and its target, and never shade the other lights (shadow linking).
  Bolder Window pattern. Old light-node gobos are cleared when you pick again.
- HDRI lighting: an **HDRI** chip under the light chips. Blender's eight
  built-in images (CC0, Poly Haven) or your own .hdr/.exr, with Brightness,
  Rotation, Tint and "Show behind the studio" (off: the camera keeps the
  studio colour). Your own world comes back when you turn it off.
- Fixed: leaving a material preset and coming back reset your edits to the
  preset's values. Each part now remembers its edits per preset (also after
  Original); clicking the current preset keeps them; Reset starts over.

## 0.5.0

- Shots included in Shoot show a red dot instead of a tick.
- Fixed: a slider whose knob sat at either end (Clearcoat starts at 1.00)
  couldn't be grabbed by its knob. Grabbing a knob no longer jumps the value.
- Text always fits its button: it shrinks slightly, then ends in "…"; switch
  labels wrap; card titles make room for their tag. Paths keep their end.
  Material parts are named after the object, not "Plinth_Sphere_0".
- Switching to Cycles shows Cycles live in the viewport (with the viewport
  denoiser); Eevee goes back to the studio view.
- Eevee quality: glass-like materials (yours too) get ray-traced refraction
  and see-through shadows, as a thin pane or a solid by their shape; Clear
  glass picks pane or solid by itself; sharper Studio/Gloss presets (probe
  resolution, shadows, traces). New "Car studio" look.
- Automotive materials from the UDIN SHADERS free pack (MIT, bundled with its
  licence, adapted to world-space patterns): Metallic, Solid gloss, Pearl and
  Matte paint, new Heavy flake and Iridescent; Chrome; Window and Privacy
  glass; Piano black; Carbon fibre, new Matte carbon and Forged carbon. Each has
  wear controls (smudges, scratches, stains). Plinth's own carbon stays as
  "Fine carbon weave".

## 0.4.0 — the redesign (docs/design/REDESIGN.md)

- Solid black panels, brighter secondary text, 11 px labels, and the engine
  preset reads "Eevee · Studio". Colours now draw as designed: Blender's
  viewport is linear, so every colour used to come out washed out.
- Motion: presses, hover and selection fade and slide (all under a quarter of a
  second, nothing in the scene animates). **Reduce motion** in the add-on
  preferences turns it off.
- Type exact values: double-click any slider's number. Shots rename inline.
- Shot bar: active and "included in Shoot" are now separate (a tick on each
  shot); a ⋯ menu renames, duplicates, moves and deletes shots; Shoot says what
  it will do ("Shoot Hero", "Shoot 3 shots"), shows progress with a stop button,
  and ends with "Open folder". Long bars collapse, then page.
- Edit pages replace "More": every step's settings on the card, three tabs
  each. New settings: backdrop colour palette and hex, floor reflection and
  roughness, floor edge fade; light gels, spread, visible in shots, gobo scale,
  rotation and softness, Dots and Stripes gobos; focus on the nearest surface
  and focus offset; turntable on any shot (frames, direction, start angle);
  16-bit, Standard/AgX, a mask next to transparent shots, file naming (shot,
  product + shot, + date), open the folder when done, GPU/CPU, denoise, sample
  boost. Each page has Reset.
- Cleaner cards: Looks as an even grid marking the last look; light chips show
  the light's colour and wattage; Duplicate/Remove in a ⋯; the material part is
  the card title (Body ▾); preset tiles with colour swatches; toggles are
  switches.
- Changing step frames the product (Camera and Shoot look through the active
  shot), with an Orbit │ Shot switch top left. Building the studio makes Hero
  the active shot. First run explains the two steps to a studio.
- Files are named after the shot's name, so renamed shots save under it.

## 0.3.1

- Fixed: the Camera step stopped responding to clicks (pill, shot dock,
  aspect, sliders) after selecting a shot. Building the floating UI wrote data
  where Blender forbids it; it's now read-only, and an error can never block
  input again.
- Glass, paint and metal now reflect the actual studio (cove, Plinth lights,
  reflection probe) in the Plinth viewport instead of Blender's preview HDRI.
  Build Studio adds a neutral world if the scene has none.
- Carbon fibre: a real 2x2 twill sized in centimetres (0.1-20 cm, default
  1 cm), projected so it doesn't stretch on curved panels, with the weave's
  angle flip working in Eevee.
- Procedural patterns keep their real size on models imported at another
  scale (e.g. 0.01 FBX).
- Tyre rubber rebuilt: rubbery reflectance, micro texture, satin sheen, dust
  settling in crevices, and a Tyre shine control. New presets: Dusty tyre,
  Dressed tyre.

## 0.3.0

- Fixed: applying a finish to a second object turned the first one black;
  objects sharing a mesh overwrote each other's material; multi-material
  objects always had their first slot changed; renaming orphaned materials.
- Procedural material library for automotive and product work: car paint
  (metallic flake, pearl flip, candy, clearcoat with orange peel, satin wrap,
  matte), metals (chrome, polished alloy, brushed aluminium/steel, gunmetal,
  gold, copper, burnt titanium), glass (clear, window, privacy tint, frosted,
  headlight lens, amber lens) and trim (plastics, tyre rubber, carbon fibre,
  leather). Every control is editable.
- Glass refracts in real time in Eevee: raytraced refraction, thin pane vs
  solid, light through glass in shadows, absorption tint.
- Material card: parts (material slots), type, presets and three key sliders;
  *Edit material* opens every control. Same editor in the sidebar.
- Targets: spawn them, drag their icon over any surface, aim any light or
  shot at one. Shots focus on their target with *Blur background*.
- Lights: add, duplicate and remove; every light is fully editable.
- Shots: add from the current view (+ in the shot dock), duplicate, remove,
  change aspect (4:5, 1:1, 16:9, 3:2, 9:16).
- Cards shed extra rows on small viewports instead of disappearing.

## 0.2.0

- Plinth has its own workspace: one full-window viewport with the floating
  controls. Open it from the sidebar (N › Plinth › Open Plinth Studio);
  Layout and your other workspaces are left alone.
- Floating controls over the viewport, following the design canvas: step
  pill, Eevee/Cycles switch with preset menu, light chips, an inspector card
  per step (three controls + More), shot dock with Shoot button, status line,
  light labels and shot-frame label in the viewport. Hide with
  *Floating controls*.
- Light shapes: Softbox, Strip, Spot, Ring. Warmth in Kelvin.
- Camera card: lens, height and distance for the active shot.
- Light painting: click the product to place the highlight; Shift moves the
  light; Esc puts it back.
- Finishes: Gloss, Metal, Glass, Matte, keeping the product's colour; also in
  the Asset Browser.
- Backdrop is now a Geometry Nodes cove with live sliders.
- Gobos for any light: Window, Blinds, Leaves (Cycles).
- Eevee presets tuned to match Cycles, plus a reflection probe in the studio.
  A parity test keeps them matched.
- Compare with Cycles: swipe between a Cycles render and the live viewport.
- Turntable shot: one full turn, one image per frame.
- Auto preset: picks the Eevee preset from the product's materials and motion.
- Studio looks: Clean white, Dark luxe, Soft daylight, plus your own.

## 0.1.0

- Engine presets and hybrid mode, cove backdrop, 3-light rig, 4 shot presets,
  batch Shoot, light-painting maths, sidebar panel.
