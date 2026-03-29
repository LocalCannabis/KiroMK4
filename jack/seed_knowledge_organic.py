#!/usr/bin/env python3
"""
jack/seed_knowledge_organic.py — Organic cultivation knowledge for Tim's setup.

Covers:
  Source 11: Gaia Green Amendments + Promix (Tim's specific setup)
  Source 12: Buildasoil / Coot's Mix Living Soil Principles
  Source 13: KIS Organics / PNW Organic Cannabis
  Source 14: Harley Smith / NPK University — Nutrient Chemistry
  Source 15: Environmental Management (Dr. Coco / CFC framework)
  Source 16: Promix Technical Reference (Premier Tech)

Usage:
    python -m jack.seed_knowledge_organic
    python -m jack.seed_knowledge_organic --source gaia_green
    python -m jack.seed_knowledge_organic --list
"""

from __future__ import annotations

import argparse
import logging

from .config import load_jack_config
from .knowledge import KnowledgeStore, EmbeddingProvider

logger = logging.getLogger("jack.seed_knowledge_organic")


# =============================================================================
# Source 11 — Gaia Green Amendments + Promix
# Tim's exact setup: WP420/Promix HP peat base + 4-4-4 / 2-8-4
# =============================================================================
GAIA_GREEN_PROMIX_REFERENCE = """
## Gaia Green Amendments in Promix / Peat-Based Media — Tim's Setup

### Setup Context
Tim grows in a 2x2 tent (Grow A, indoor) using WP420 — a Canadian peat-based
medium equivalent to Promix HP. He amends with Gaia Green organic dry fertilizers:
4-4-4 All Purpose (vegetative) and 2-8-4 Power Bloom (flower).

This combination — peat medium + Gaia Green amendments — is one of the most
common organic cannabis setups in Canada, and for good reason: Gaia Green
products are Canadian-made, widely available, OMRI-listed, and specifically
formulated for this type of application.

### About the Medium: WP420 / Promix HP

Promix HP (High Porosity) and WP420 are both Canadian Sphagnum peat-based
growing media with no initial fertilizer charge. They are:
- pH-adjusted to 5.5–6.5 (slightly acidic, appropriate for cannabis)
- High porosity: 65–80% air-filled pore space at container capacity
- Low CEC (cation exchange capacity): ~10–15 meq/100g — very limited
  nutrient-holding capacity compared to real soil (20–60 meq/100g)
- No meaningful inherent microbiology on opening — it is essentially
  a physically inert, well-draining substrate

The low CEC matters: unlike real soil, peat cannot buffer large nutrient
additions. This means you cannot over-amend heavily and expect the medium
to hold excess nutrients for later — you'll get pH drift and potential
nutrient lockout. Stay conservative with amendment rates.

### Gaia Green 4-4-4 All Purpose

NPK: 4% N, 4% P2O5, 4% K2O (balanced slow-release)
Other: 2% calcium, 0.5% magnesium, 0.5% sulfur, micronutrients
Ingredients: feather meal, bone meal, blood meal, rock phosphate, mined
potassium sulfate, humus, glacial rock dust, greensand, kelp meal

Release profile: Slow-release organic. The nitrogen fraction is primarily
from feather meal and blood meal, which require microbial action to
mineralise. In a biological medium this takes 2–4 weeks to ramp up.
In fresh Promix with minimal biology, the first 1–2 weeks after mixing
may show slow nitrogen availability — this is normal.

Recommended rates (Gaia Green guidelines, adapted for 2x2/container):
- Pre-mix (incorporating into fresh Promix): 2–3 cups per cubic foot
  of medium. For a 5-gallon container: approximately 0.5–0.75 cups (120–180g).
  For a 3-gallon container: approximately 0.3–0.5 cups (80–120g).
- Top dress (once plants are established, every 3–4 weeks in veg):
  1–2 tablespoons per gallon of container volume. For a 5-gallon: 5–10 tbsp.
- Do not exceed 3 cups per cubic foot at initial mix — over-amending causes
  pH issues and can lock out other nutrients.

### Gaia Green 2-8-4 Power Bloom

NPK: 2% N, 8% P2O5, 4% K2O (bloom-oriented)
Other: 1.5% calcium, 0.5% sulfur, 2% magnesium
Ingredients: bone meal, fish bone meal, rock phosphate, potassium sulfate,
humus, kelp meal, glacial rock dust

Release profile: Slightly faster P availability than 4-4-4 due to fish bone
meal fraction, but still organic and biology-dependent.

Recommended rates:
- Transition (at flip): Begin working 2-8-4 into the top inch of medium
  or apply as a top dress to start the mineralisation process. Use
  approximately 1 tbsp per gallon of container volume.
- Early to mid-flower top dress: 1–2 tbsp per gallon every 3–4 weeks.
  Do not pile on — over-application of P in organic systems can cause
  micronutrient lockout, especially zinc and iron (same finding as
  Westmoreland & Bugbee 2022 in hydroponic systems).
- Late flower (weeks 6–8): Reduce or stop feeding as the plant approaches
  maturity. The medium will continue mineralising existing amendments.

### 4-4-4 + 2-8-4 Transition Schedule (Veg → Flower)

Weeks 1–2 (after transplant): Medium pre-charged with 4-4-4. Water with
plain pH-adjusted water. Let biology establish.

Weeks 3 onward in veg: Top dress with 4-4-4 (1 tbsp/gallon container
volume) every 3–4 weeks. Water in lightly after application.

Flip (Day 1 of 12/12): Top dress with 2-8-4 (1 tbsp/gallon). Continue
4-4-4 at reduced rate (0.5 tbsp/gallon) for first 2 weeks of flower
to support nitrogen needs during stretch.

Flower weeks 3–6: 2-8-4 only at 1–2 tbsp/gallon every 3–4 weeks.

Flower weeks 7+: No new top dressing. Let existing amendments deplete.
If the plant is yellowing too early (week 5 or earlier), it may need
a light top dress to sustain the grow.

### pH Management in Promix with Gaia Green

Target pH: 6.0–6.8 (optimal 6.2–6.5 for broad organic nutrient availability)
Note: Gaia Green amendments as they mineralise generate organic acids that
can gradually lower pH in peat medium. Monitor runoff pH every 2–3 weeks.

Signs of pH drift:
- Below 5.8: Iron and manganese toxicity risk; phosphorus availability
  drops sharply; tips may show burned appearance
- Above 7.0: Iron, manganese, zinc deficiencies; medium buffers poorly

Correction:
- pH too low: Dolomite lime top dress (1–2 tbsp per 5-gallon) — slow but
  effective. Agricultural lime also works but slower.
- pH too high: Water with slightly acidified water (pH 5.5–6.0) for
  a few waterings to bring it back down. Uncommon issue in peat.

### Watering Promix with Organic Amendments

Unlike hydroponics, you do not need to track runoff EC closely in an
organic Promix grow. The amendments are slow-release and not in ionic form.

Watering rule of thumb:
- Water to 5–10% runoff to prevent salt accumulation from water minerals
  and to confirm full saturation.
- In a 5-gallon container: approximately 1–1.5 litres per watering.
- Frequency: Allow top 2 inches to dry before re-watering. In a 2x2 with
  a single plant in mid-flower, this typically means every 2–3 days.
- Never let Promix fully dry out — it becomes hydrophobic and is very
  difficult to re-wet evenly. A small amount of runoff is your signal that
  you've hit full saturation without creating waterlogging.

Promix vs. living soil watering difference:
In living soil you're trying to maintain 3–7 on a moisture scale continuously.
In Promix, the medium has much higher air porosity and you can let it get
drier between waterings without harming the biology, because there is less
biology to protect. The dry-back cycle actually benefits oxygen delivery to
roots in peat.

### Biological Inoculants for Promix Organic Grows

Since Promix has no inherent biology, it's worth introducing:
1. Mycorrhizal inoculant at transplant — apply directly to root ball.
   Products: Mykos, Rootwise Mycrobe Complete, Recharge.
   These establish AMF (arbuscular mycorrhizal fungi) that extend the
   effective root zone and help release phosphorus from bone meal.

2. Bacterial inoculants (Bacillus spp.) — help mineralise organic N.
   Apply as a water-in solution at transplant and monthly.

3. Compost tea (optional) — 1x/month drench helps establish a broader
   biological community that accelerates amendment mineralisation.

Without any biology, Gaia Green in Promix will still work — the amendments
will mineralise with moisture and time — but with biology, the process is
faster and more complete.

### Common Issues in Gaia Green + Promix Grows

1. Slow start / pale yellow seedlings in weeks 1–2
   Cause: Biology hasn't established; nitrogen from feather/blood meal not
   yet mineralised. This is normal in a fresh, uncolonised peat medium.
   Solution: Water with a dilute fish/kelp tea (1 tbsp each per gallon) to
   add some available N while the slow-release ramps up. Or wait — it
   resolves itself.

2. Purple stems in early veg
   Cause: Usually phosphorus or magnesium deficiency, or pH drift below 6.0.
   Check pH first. If pH is in range, apply a light top dress of 2-8-4
   (which contains more P and Mg than 4-4-4).

3. Nitrogen toxicity (clawing, dark green leaves)
   Cause: Over-application of 4-4-4 or a sudden mineralisation spike when
   the biology kicks in. Reduce top dress frequency and amount.
   Flush once with plain pH-adjusted water.

4. Yellow leaves in flower (before week 6)
   Cause: N deficiency — either under-applied 4-4-4, or biological
   slowdown. Top dress with a little 4-4-4 (0.5 tbsp/gallon) alongside
   the 2-8-4 to maintain N in early-to-mid flower.

5. Slow flowering / poor bud density
   Cause: Often a light or environment issue, not fertilizer. Check VPD,
   DLI (target 30–40 mol/m²/day in flower), and temperature differential
   (a 5–10°C day/night drop in late flower promotes bud densification).

### Sources
- Gaia Green Organics product specifications and feeding guides.
  gaiagreen.ca (Abbotsford, BC)
- Premier Tech Horticulture. Promix HP and WP420 product data sheets.
  pthorticulture.com
- Compiled from Canadian grower community best practices and
  direct application of academic findings (Caplan, Westmoreland/Bugbee)
  to organic slow-release grows.
"""


# =============================================================================
# Source 12 — Buildasoil / Coot's Mix Living Soil Principles
# Jeremy Silva's framework for living soil containers and watering
# =============================================================================
BUILDASOIL_LIVING_SOIL_REFERENCE = """
## Buildasoil / Coot's Mix — Living Soil Container Principles

### Source Overview
Jeremy Silva (Buildasoil) is the most comprehensive free educator on living soil
indoor growing. His blog (buildasoil.com/blogs) and YouTube channel provide the
foundational framework that most Canadian organic cannabis growers work from.

The "Coot's Mix" (named after Clackamas Coot, the originator on online grow
forums) is the canonical recipe that Jeremy has popularised and adapted.

URL: buildasoil.com
YouTube: youtube.com/buildasoil

### The Coot's Mix Foundation Recipe

The benchmark recipe from Clackamas Coot / Buildasoil:

Base (1:1:1 ratio):
- 1 part Sphagnum Peat Moss (or coconut coir)
- 1 part Aeration (pumice, lava rock, rice hulls, perlite)
- 1 part Quality Compost (finished, known source — compost quality is
  everything; municipal compost often not suitable)

Amendment package (per cubic foot of base):
- 1/2 cup Karanja Cake (NPK + micronutrients + insect pest defence)
- 1/2 cup Kelp Meal (NPK + micronutrients + growth hormones)
- 1/2 cup Crustacean Meal/Crab Meal (calcium, nitrogen, chitin)
- 4 cups Mineral Mix:
  - 2 cups Basalt (paramagnetic rock dust, high in micronutrients)
  - 1 cup Gypsum (calcium + sulfur)
  - 1 cup Oyster Shell Flour (calcium carbonate, pH buffer)

Total amendments per cubic foot: 1.5–3 cups total fertilizers. Less is more —
you can always add more later via top dressing; you cannot remove an
over-application.

### Scaling to Tim's 5-Gallon Container

A 5-gallon container = approximately 0.67 cubic feet.

Scaled Coot's Mix for a 5-gallon:
- Peat moss: 1.67 gallons (about 6L)
- Pumice/aeration: 1.67 gallons
- Quality compost: 1.67 gallons
- Karanja: 1/3 cup
- Kelp meal: 1/3 cup
- Crab/crustacean meal: 1/3 cup
- Basalt: 1.3 cups
- Gypsum: 2/3 cup
- Oyster shell: 2/3 cup

Note: Tim's current setup uses Gaia Green in Promix rather than a from-scratch
living soil. The Coot's Mix is the eventual upgrade path — when Tim is ready
to build his own medium rather than using a pre-made peat base.

### Lightweight Recipe (Recipe #2 — Lower Compost Risk)

When local compost quality is uncertain, use a lighter base:
5 parts peat : 2 parts aeration : 1 part compost (lower risk of
imbalanced compost wrecking the recipe).

Per cubic foot:
- 1/2 cup Karanja Cake
- 1/4 cup Kelp Meal
- 1/2 cup Crustacean Meal
- 1/2 cup Gypsum
- 1 cup Basalt

This lighter recipe may need top dressing or compost tea supplementation
but is more forgiving.

### The Buildasoil Approach to Top Dressing

Philosophy: Feed the soil, not the plant. Top dressings feed the soil
biology, which then feeds the plant through nutrient mineralisation.

Standard top dress schedule:
- Every 3–4 weeks: Small amounts of amendments on the soil surface
- Worm castings: 1/4–1/2 cup per 5-gallon (always safe, broad benefit)
- Kelp meal: 1 tbsp per 5-gallon (micronutrients, growth hormones)
- Malted barley powder: 1 tsp per gallon of container volume
  (amylase enzymes trigger biological cycling — highly recommended)
- Cover crops (clover, triticale) on the soil surface: protect biology
  and provide ongoing organic matter inputs

### How To Water Living Soil (Jeremy Silva's Framework)

Source: buildasoil.com/blogs/news/how-to-water-living-soil-over-water-vs-under-water

The most important skill in living soil growing. "This is the 20% that causes
80% of the issues. Learn to water and success is much closer."

Core rule: Water at 5–10% of soil volume per watering.
- 5-gallon container: 0.25–0.5 gallons (approximately 1–2 litres) per watering
- 10-gallon container: 0.5–1 gallon per watering
- 15-gallon container: 0.75–1.5 gallons per watering

Moisture scale: Think of soil moisture as a 1–10 scale.
1 = bone dry. 10 = muddy wet. Target range: 3–7 throughout the grow.

Frequency guidance:
- Big container with small plant: Water less often (easy to overwater)
- Small container with big plant: Water more often (can't hold enough)
- 10-gallon with one mature plant in flower: Daily or every other day
- 5-gallon with one plant in late veg: Every 2–3 days
- 15-gallon no-till bed: Multiple plants, may water daily in small amounts

Diagnostic checks before watering:
1. Lift or tilt the container — feel the weight
2. Thump the side — hollow sound = under-watered, dense/wet = adequate
3. Check top 1–2 inches — if dry, water; if moist, wait
4. Laser the leaf temperature — low canopy temp = don't water aggressively

Best practice:
- Water when lights turn on (morning), at a consistent time
- Use a pump sprayer for small gardens — gentle, even, like rain
  (not fast and rushed)
- Less water, more often is better than large infrequent waterings

VPD affects watering frequency:
- Low VPD (high humidity): Plant transpires less → water less often
- High VPD (low humidity/hot): Plant transpires more → water more often
- Cold root zone: Biological activity and water uptake both slow → water
  less even if medium feels dry

### Supplemental Inputs (Buildasoil's Favourite Add-ons)

These can be watered in or applied as foliar sprays:
- Aloe vera (freeze-dried powder or fresh gel): Saponins + growth
  stimulants; help with root development and biological activity
- Yucca extract (ThermX-70 or similar): Breaks hydrophobic tension
  in peat; wetting agent; saponins feed soil biology
- Coconut water: Cytokinins (plant growth hormones); helps roots
- Actively Aerated Compost Tea (AACT): Multiplied biology;
  recipe: 1 cup compost + 1 tsp unsulfured molasses + 1 tsp kelp,
  5 gallons water, aerated 24–48 hours

### "Cooking" the Soil

When you mix fresh living soil (especially with blood meal, guano, or
high-nitrogen inputs), the microbial activity heats the pile. This is normal.
- Recipes with blood meal/guano may heat to 60–70°C — need turning every
  few days, cook for 2–4 weeks before use
- Gaia Green + peat recipes (Tim's setup) don't need extended cooking —
  can plant within 1–2 days of mixing. The low-nitrogen profile doesn't
  trigger the same heat spike.

### Pest Management in Living Soil (Buildasoil IPM)

Source: buildasoil.com/blogs/news/how-to-keep-a-clean-garden-pest-free

Prevention > treatment:
1. Start clean: New or pasteurised containers each run
2. Sprinkle diatomaceous earth on soil surface (physical barrier)
3. Introduce predatory insects early (ladybugs, predatory mites)
4. Use Bacillus thuringiensis (Bt) as preventative in flower
5. Karanja cake in the soil mix has documented pest-deterrent properties
   (azadirachtin compounds similar to neem — systemic through the roots)

The philosophy: High brix, healthy plants are resistant to pests.
Fix the growing conditions and most pest pressure self-resolves.

### References
- Jeremy Silva. BuildASoil blog and YouTube channel.
  buildasoil.com/blogs/news | youtube.com/buildasoil
- Clackamas Coot. Original forum posts on living soil recipes.
  Compiled and popularised by Jeremy Silva at BuildASoil.
- Published recipe guides: buildasoil.com/blogs/news/17627464-build-a-soil-from-scratch-in-2-simple-steps
- Watering guide: buildasoil.com/blogs/news/how-to-water-living-soil-over-water-vs-under-water
"""


# =============================================================================
# Source 13 — KIS Organics / PNW Organic Cannabis
# Tad Hussey's framework: water-only soil, re-amendment, podcast
# =============================================================================
KIS_ORGANICS_REFERENCE = """
## KIS Organics — Pacific Northwest Organic Cannabis (Tad Hussey)

### Source Overview
KIS Organics (Keep It Simple Organics) is run by Tad Hussey out of the Pacific
Northwest (Washington State). The climate context — marine, cool, wet winters,
dry warm summers — closely mirrors Vancouver, BC.

Tad hosts "Cannabis Cultivation and Science" podcast (free, available on major
podcast platforms) and publishes detailed articles on the KIS Organics blog.

Website: kisorganics.com
Podcast: "Cannabis Cultivation and Science" — Spotify, Apple Podcasts, etc.

### Water-Only Soil Mix Philosophy

The KIS Organics approach is to build a soil rich enough and biologically active
enough that you can water it without adding any additional liquid nutrients for
the entire grow. This is the ultimate expression of "feed the soil, not the plant."

The KIS Organic water-only mix:
- Base: 1/3 peat/coir, 1/3 compost, 1/3 aeration (pumice/perlite)
- Mineral pack: Oyster shell flour, basalt, glacial rock dust, gypsum
- Organic N: Feather meal, neem/karanja cake, kelp meal, crab meal
- P sources: Bone meal, rock phosphate
- Biology: Worm castings (significant amount — 20–30% of total mix)
- Innoculant: Mycorrhizal fungi, Trichoderma, bacterial inoculants

Key principle: Worm castings are not just nutrients — they are biology.
A mix with 20–30% quality worm castings has a dramatically more active
microbial population than a mix without them.

### Re-Amendment: Reusing and Refreshing Soil Between Runs

This is where Tad's content is particularly valuable for Tim's eventual
no-till progression. Instead of throwing out medium and starting fresh:

After harvest, the root ball is removed (or left to decompose in no-till).
The medium is refreshed with:
- 25–30% by volume of new compost or worm castings
- A new dose of mineral amendments: basalt, oyster shell, kelp
- A light dose of organic N: feather meal or neem cake
- Let the refreshed medium rest 2–4 weeks before replanting

This approach:
1. Maintains the biological community already established in the medium
2. Is significantly cheaper per run than starting with fresh medium
3. Improves over time — the soil gets better with each run as biology diversifies
4. Reduces waste (consistent with Canadian/BC grower values)

For Tim's Grow A (small container indoor):
- Can refresh Promix + Gaia Green after harvest by adding 20% worm castings
  and a new Gaia Green charge (4-4-4 at 1 cup/cubic foot)
- Let sit 1–2 weeks before replanting
- Expect improvement from run to run as mycorrhizae and bacteria establish

### No-Till Container Growing

No-till means not disturbing the root zone between runs. The previous
plant's roots are left to decompose (or cut at the base), which:
- Feeds soil biology as the root mass decomposes
- Creates channels in the medium that improve drainage and root penetration
- Builds organic matter over time

In containers, no-till works best with:
- Fabric pots (allows gas exchange at the root zone)
- 10-gallon or larger containers (more stable biology in bigger volume)
- Cover crops on the soil surface to protect biology between runs

Tad's framework for no-till container setup:
1. Plant cover crop on soil surface (white clover, triticale, phacelia)
2. Cover crop protects soil surface from light and drying
3. Mulch on top of cover crop (straw, wood chips) to retain moisture
4. At end of grow: harvest, cut stem at base, let roots decompose
5. Top dress with compost + kelp + basalt, plant next crop

### Compost Tea in the KIS Organics System

AACT (Actively Aerated Compost Tea) is a central tool:
Basic recipe (5-gallon batch):
- 1 cup finished compost or worm castings
- 1 tsp unsulfured molasses (bacterial food)
- 1 tsp kelp meal
- Optional: 1 tsp fish hydrolysate, aloe powder
- Brew 24–48 hours with strong aeration
- Use within 4–6 hours of completing brew

Application:
- Soil drench: Apply at transplant, then every 3–4 weeks through veg/flower
- Foliar: Dilute 1:1 with water, spray leaves at lights-off (early growth
  stages only — do not foliar spray in flower if Botrytis risk is present)

Tad's perspective: AACT is not primarily about adding nutrients to the plant —
it's about seeding a diverse microbial population that helps break down
organic amendments and protect root health. The most valuable outcome is a
living soil that supports itself.

### Biological Fertility Principles (KIS Organics)

1. Carbon:Nitrogen ratio: Keep C:N in the medium around 25–30:1 for
   balanced biological activity. Too much C (wood chips, straw without
   N) ties up nitrogen in microbial biomass; too much N (blood meal)
   causes hot, fast breakdown and plant burn.

2. Cation Exchange Capacity (CEC): Building CEC is the goal of no-till.
   Over multiple runs, decomposing root mass and increasing organic
   matter content raises CEC, allowing the medium to hold more nutrients
   and resist pH swings. Fresh peat has CEC ~10. After 3–4 no-till runs,
   CEC can rise to 20–30+.

3. Mycorrhizal investment: Once mycorrhizal fungi establish, phosphorus
   availability dramatically increases from bone meal and rock phosphate.
   This is why no-till growers often see noticeably improved bud density
   and quality from the second run onward.

4. Diversity = resilience: A diverse microbial community (bacteria, fungi,
   protozoa, nematodes) is more stable and functions better under stress
   than a simple community. Adding diverse compost sources, cover crops,
   and organic matter inputs over multiple runs increases diversity.

### PNW-Specific Notes (from Tad Hussey's regional context)

Water quality in Metro Vancouver:
- Vancouver tap water is glacial-sourced, very soft (hardness ~25–40 mg/L
  as CaCO3), low alkalinity, slightly acidic pH (6.8–7.2)
- Very low calcium and magnesium content
- In a peat-based organic grow, this matters: the medium's pH buffering
  relies on the amendments (oyster shell, gypsum) rather than the
  water alkalinity — which is actually beneficial (water won't push pH up)
- Add calcium and magnesium through amendments, not through water
- Chlorine/chloramine: Vancouver uses chloramine (not just chlorine) for
  disinfection. Chloramine does NOT off-gas by letting water sit overnight
  the way chlorine does. If treating for biology, use a carbon block filter
  or ascorbic acid (vitamin C) dechlorination.

Outdoor BC soil temperature:
- Cannabis roots should not be planted until soil is consistently >15°C
- Metro Vancouver soil temperature at 10cm depth typically hits 15°C in
  mid-May (May 10–20 range)
- Cold soil (<12°C) dramatically slows microbial activity and nutrient
  mineralisation — even in a perfect mix, cold roots mean slow plants

### Sources
- Tad Hussey. KIS Organics blog and "Cannabis Cultivation and Science" podcast.
  kisorganics.com | Available on Spotify, Apple Podcasts
- Cannabis Cultivation and Science Podcast (various episodes 2018–2025):
  episodes covering water-only soil, re-amendment protocols, compost tea,
  no-till container growing, and soil chemistry fundamentals
- KIS Organics Water-Only Soil Mix recipe (published on kisorganics.com)
"""


# =============================================================================
# Source 14 — Harley Smith / NPK University — Nutrient Chemistry
# Plant nutrition science: CEC, pH buffering, organic vs synthetic
# =============================================================================
HARLEY_SMITH_REFERENCE = """
## Harley Smith / NPK University — Nutrient Chemistry and Plant Science

### Source Overview
Harley Smith is a plant scientist (M.S. in plant biology) who has worked for
multiple nutrient companies and presents detailed, chemistry-grounded explanations
of plant nutrition for cannabis growers. His "NPK University" video lecture series
is available free on YouTube.

Key topics: Cation exchange capacity, pH buffering in organic substrates,
cation competition, organic vs. synthetic nutrient uptake, organic acid
chemistry, secondary metabolite production.

YouTube: Search "NPK University Harley Smith" — multiple full-length lectures
available. Plant nutrient science explained at university level for growers.

### Cation Exchange Capacity (CEC) — The Foundation

CEC is the substrate's ability to hold positively charged nutrient ions
(cations: Ca²⁺, Mg²⁺, K⁺, NH₄⁺, Fe²⁺, etc.) and make them available
to roots.

CEC is expressed in milliequivalents per 100g (meq/100g) or
centimoles of charge per kilogram (cmolc/kg — same unit).

Substrate CEC guide:
- Sand: 1–5 meq/100g (almost no buffering)
- Sphagnum peat (Promix HP / WP420): 10–15 meq/100g (limited)
- Perlite/pumice: near 0 (structurally inert)
- Compost: 30–70 meq/100g (highly variable; high-quality compost is high)
- Worm castings: 50–100 meq/100g (excellent CEC)
- Real agricultural soil: 20–40 meq/100g (variable)
- Biochar: 20–400 meq/100g (highly variable; most unactivated biochar
  is near the low end before it's colonised)

Why CEC matters for Tim's setup:
Promix HP has CEC ~10–15. This means the medium cannot hold much ionic
nutrition. In synthetic growing, this means you're feeding every watering
because nutrients wash out. In organic growing with dry amendments,
nutrients are released slowly by biology and held partly in microbial
biomass — but pH management becomes more critical because there's less
buffering capacity to absorb acidic/alkaline shifts.

### The Cation Balance — Why Ratios Matter More Than Total Amount

On any cation exchange site, cations compete for attachment. If one
cation is present in large excess, it can displace others.

Key competition pairs:
- Calcium (Ca) vs. Magnesium (Mg): High Ca suppresses Mg uptake.
  Calcium-dominant fertilisers (especially bone meal, oyster shell)
  without Mg correction can cause Mg deficiency.
  Target soil ratio: Ca:Mg approximately 5–7:1 by equivalent weight.
  Gaia Green's amendments are Ca-heavy; supplemental Mg may be needed
  in later runs. Signs: Interveinal chlorosis starting at older leaves.

- Potassium (K) vs. Calcium and Magnesium: High K suppresses both.
  Bloom nutrients high in K (kelp, potassium sulfate, many bloom formulas)
  can drive K:Ca imbalance in late flower. Signs: Calcium deficiency in
  rapidly expanding tissue despite adequate Ca in medium.

- Ammonium (NH₄⁺) vs. Calcium: High ammonium from blood meal or
  feather meal mineralisation temporarily displaces calcium from
  exchange sites. This is why nitrogen toxicity and calcium deficiency
  can appear simultaneously after a large N top dress.

- Iron (Fe) vs. Phosphorus: High P suppresses Fe uptake by forming
  insoluble iron phosphate complexes. This is particularly relevant
  when using bone meal + high P bloom amendments together. Sign:
  Iron deficiency (interveinal chlorosis in young leaves) despite
  normal pH.

### pH and Nutrient Availability in Organic Systems

The classic pH availability chart shows each nutrient's optimal pH range.
For cannabis in organic/peat media:

Optimal pH: 6.0–6.8
- All major (N, P, K) and secondary (Ca, Mg, S) nutrients available
- Micronutrients (Fe, Mn, Zn, B, Cu, Mo) all accessible

Below 5.8:
- P availability declines rapidly
- Fe and Mn become increasingly available (can become toxic)
- pH below 5.5: risk of aluminium and manganese toxicity
- Microbial activity declines (bacteria prefer pH 6–7)

Above 7.0:
- Fe, Mn, Zn, B, Cu all become rapidly less available
- Phosphorus forms insoluble calcium phosphate
- Molybdenum becomes more available (rarely a problem)

Organic acids and pH buffering:
When organic amendments (feather meal, bone meal, kelp) decompose,
microorganisms produce organic acids (humic acids, fulvic acids,
carbonic acid). These acids:
1. Gradually lower substrate pH — this is why Promix pH can drift
   downward over a long organic grow
2. Complex with metal cations (Fe, Mn, Cu, Zn) to keep them in solution
   at otherwise slightly high pH — this is the "chelation" effect of
   organic acids and humus
3. Improve cation exchange capacity of the medium over time as they
   form humus

Fulvic acid vs. humic acid:
- Fulvic acid: Low molecular weight, highly soluble, rapidly available
  to roots. Found in worm castings, compost tea. Chelates micronutrients
  and aids nutrient transport across root membranes.
- Humic acid: High molecular weight, less soluble, slower-acting.
  Builds CEC and soil structure. Found in leonardite, high-quality
  composts.

### Organic Nitrogen Mineralisation — The N Cycle

Organic nitrogen sources (feather meal, blood meal, fish meal, compost)
contain nitrogen in complex organic molecules (proteins, amino acids).
The release sequence:

1. Bacteria and fungi produce enzymes (proteases, ammonium oxidisers)
   that break down protein → ammonium (NH₄⁺)
2. Ammonium is plant-available and held on CEC sites
3. Nitrifying bacteria (Nitrosomonas) convert NH₄⁺ → nitrite (NO₂⁻)
4. Nitrobacter converts nitrite → nitrate (NO₃⁻)
5. Plant roots take up NH₄⁺ and NO₃⁻ directly

Timeline: Steps 1–5 take 2–6 weeks in warm conditions. This is why
organic grows have a lag period at the start — the plant is waiting for
biology to mineralise the N.

Factors affecting mineralisation speed:
- Temperature: Optimal 25–30°C; below 15°C, drastically slowed;
  below 10°C, nearly stopped
- Moisture: Wet-dry cycling promotes biological activity; consistently
  saturated conditions favour anaerobic bacteria that denitrify N
- Carbon:Nitrogen ratio: C:N >30 means biology is N-limited; adds
  more N-rich material. C:N <15 means fast N release, potential burn.

### Secondary Metabolites — Terpenes and Cannabinoids

Harley Smith's perspective on secondary metabolite production:
Cannabis produces terpenes and cannabinoids as secondary metabolites —
they are not primary for growth (which needs N, P, K) but are produced
in response to stresses and signals.

Key factors that upregulate secondary metabolite production:
1. Mild abiotic stress: Moderate drought stress, UV exposure (though
   UV-B for THC is not supported by Bugbee research), temperature
   stress in late flower
2. Micronutrient sufficiency: Especially zinc (terpene biosynthesis),
   iron (chlorophyll, enzyme cofactor), and molybdenum (nitrogen
   metabolism)
3. Potassium availability: K is critical for sugar transport (phloem
   loading) and starch-to-sugar conversion in ripening buds
4. Microbial signalling: Mycorrhizal fungi and beneficial bacteria
   produce volatile compounds and plant hormones (cytokinins, auxins)
   that directly stimulate secondary metabolite production —
   another reason biology matters in organic grows

### References
- Harley Smith. NPK University lecture series. YouTube (free).
  Search: "NPK University Harley Smith cannabis"
- Smith H. (various). Plant nutrition lectures, CEA cannabis supplement.
- Brady N.C. & Weil R.R. (2016). The Nature and Properties of Soils.
  15th edition. Pearson. (Cited source for CEC fundamentals)
"""


# =============================================================================
# Source 15 — Environmental Management (Cocoforcannabis.com framework)
# VPD, humidity, temperature, airflow for text-chat (medium-agnostic)
# =============================================================================
ENVIRONMENTAL_MANAGEMENT_REFERENCE = """
## Environmental Management for Cannabis — Comprehensive Indoor Guide

### Source Overview
The Cocoforcannabis.com (Dr. Coco) framework provides the most comprehensive
free online reference for indoor cannabis environmental control. The VPD and
environment content is fully medium-agnostic and directly applicable to Tim's
2x2 tent regardless of medium (Promix, coco, living soil).

Key topics: VPD management, humidity and temperature control, airflow and
circulation, environmental scheduling, lights-off management.

Website: cocoforcannabis.com (various environment guides)

### VPD as the Master Environmental Variable

VPD (Vapor Pressure Deficit) — the primary number to track. It unifies
temperature and humidity into one measurement that describes the
transpiration environment for the plant.

VPD = SVP(leaf) - actual VP(air)
Where SVP = saturation vapor pressure (function of temperature)
Leaf temperature is typically 2°C below air temperature in indoor grows
(adjust if running very high or very low light intensity).

VPD lookup (approximate — leaf temp = air temp - 2°C):

At 24°C air temp:
- 70% RH = VPD ~0.85 kPa (good for veg)
- 60% RH = VPD ~1.17 kPa (good for veg/early flower)
- 50% RH = VPD ~1.49 kPa (good for flower)
- 40% RH = VPD ~1.82 kPa (high — late flower OK, earlier is stress)

At 26°C air temp:
- 70% RH = VPD ~1.00 kPa
- 60% RH = VPD ~1.37 kPa
- 50% RH = VPD ~1.74 kPa
- 40% RH = VPD ~2.10 kPa (generally too high)

At 22°C air temp:
- 70% RH = VPD ~0.71 kPa (seedlings/clones)
- 60% RH = VPD ~0.99 kPa (veg)
- 50% RH = VPD ~1.26 kPa (veg/transition)

Target ranges by stage:
- Seedling/clone: 0.4–0.8 kPa (high humidity 65–80%, lower temps OK)
- Veg: 0.8–1.2 kPa
- Transition/early flower: 0.9–1.3 kPa
- Mid flower: 1.0–1.4 kPa
- Late flower: 1.0–1.5 kPa (push toward 1.4–1.5 for Botrytis prevention)

### The 2x2 Environmental Challenge

Small tents are disproportionately difficult to control because:
1. Low air volume = humidity rises fast with transpiration and drops fast
   when fan pulls are high
2. Single plant = transpiration rate varies more dramatically than a
   multi-plant canopy
3. Opening the tent for inspection causes 5–15% RH swing
4. Light distance is constrained — less ability to separate heat from PPFD

Primary control tools in a 2x2:
1. Exhaust fan speed (most important): Controls both temp and humidity.
   Higher speed = lower humidity, lower temp. Get a speed controller.
2. Passive intake (carbon filter on intake or bottom vent): Controls
   the volume of fresh air entering and how much humidity is replaced.
3. Small oscillating fan inside the tent: Controls VPD at the leaf surface
   (not the ambient measurement — moving air prevents boundary layer
   humidity from stagnating around stomata). NOT a replacement for
   proper ambient VPD management.
4. Lights-off protocols: Temperature drops 3–8°C at lights-off; humidity
   rises proportionally. This is when Botrytis risk peaks.

### Temperature Management

Temperature targets by stage:
- Veg: 22–28°C with lights on. Allow up to 26°C for fast growth.
- Flower: 20–26°C. Above 27°C degrades terpenes in late flower.
- Lights-off: Allow a 5–8°C drop (promotes bud density in late flower)
  but do NOT drop below 16°C (cold stress + condensation = Botrytis risk).
- Drying: 16–21°C, dark, 55–65% RH, slow air movement.

In Tim's grow (GrowHub with built-in fan + lights):
The GrowHub 800C's built-in 105 CFM fan continuously exhausts. In winter,
when intake air from the room is cool (18–20°C), heat from the 200W COB
may be the primary temperature management tool — lights-off temps may
need a space heater to prevent cold-related Botrytis in late flower.
In summer, the challenge reverses: room temps above 28°C mean the tent
can exceed 30°C. During heat events, raise the light, increase fan speed,
and consider running lights at night.

### Humidity Management by Stage

Veg (target 55–70%):
- If room humidity is low (below 40%), adding a small ultrasonic humidifier
  in the tent (or in the intake air path) is effective.
- In Vancouver's wet season (Oct–Mar), room humidity may already be 50–65% —
  no additional humidification needed. The main challenge is winter cold.

Flower (target 40–55%, trending toward 40% in late flower):
- A dehumidifier in the room (or inline) is the most reliable solution.
- In Vancouver's dry season (Jun–Sep), ambient RH may drop to 35–45%
  naturally — no dehumidifier needed.
- In fall (Oct–Nov), ambient RH rises to 65–80%. A room dehumidifier
  becomes important for protecting late-harvest buds from Botrytis.
- Emergency: If humidity spikes above 65% in late flower, increase fan
  speed, open the bottom vent slightly, and remove large fan leaves that
  restrict airflow through the canopy.

### Airflow and Oscillating Fans

Two distinct functions:
1. Exhaust/intake: Removes humid/hot air, brings in fresh air. Managed
   by fan speed controller. This is the primary VPD tool.
2. Oscillating fan inside tent: Moves air at leaf surface level.
   Prevents boundary layer stagnation (CO2 depletion at leaf surface),
   strengthens stems, dries leaf moisture quickly to prevent fungal
   conditions. Should create a gentle "shimmer" in the canopy, not
   aggressive waving.

The boundary layer effect:
Without oscillating air movement, a thin layer of high-humidity, low-CO2
air builds up immediately around each leaf. Even with perfect ambient VPD,
this boundary layer means the stomata "experience" worse conditions than
the ambient reading. An oscillating fan disrupts this layer and brings
stomatal environment closer to ambient VPD.

### CO2 Considerations in a 2x2

Ambient CO2: ~420 ppm (current atmospheric, slightly elevated historically).
At 400–500 ppm CO2, cannabis photosynthesis saturates around 800–1000
µmol/m²/s PPFD (per Bugbee research). Above this PPFD, plants cannot use
the extra light without additional CO2.

For Tim's 2x2 with 200W COB:
Estimated PPFD at canopy: 350–700 µmol/m²/s depending on height.
This is below saturation for ambient CO2. No CO2 supplementation needed —
and in a 2x2, CO2 systems are impractical and expensive. Ensure good fresh
air exchange (exhaust fan running) to keep CO2 from depleting below 300 ppm.

Signs of CO2 depletion in a sealed-up tent:
- Slow growth despite good nutrients and VPD
- Leaves cupping upward
- Yellowing that doesn't respond to nutrient adjustment
Fix: Open intake, increase fan speed, add fresh air exchange.

### Lights-Off Environment Protocol

The transition from lights-on to lights-off is the highest-risk period
for Botrytis:
- Temperature drops rapidly → cold surfaces condense moisture
- Stomata close, transpiration stops → humidity rises
- Action: Run the exhaust fan continuously (don't shut off at lights-off).
  A timer that reduces fan speed at lights-off rather than turning it off
  completely helps maintain enough airflow to prevent condensation.

Night temperature management:
- Target lights-off temp: 18–22°C (6–8°C below lights-on temp is ideal
  for bud density but don't go below 16°C)
- If room temp drops to 15°C at night (common in Canadian winters), the
  tent will follow. Consider insulating the tent walls or adding a small
  germination mat under the container.

### Drying and Curing Environment

These are often neglected but critical for final quality.

Drying (primary drying, days 1–14):
- Temperature: 16–21°C
- Humidity: 55–65%
- Light: Total darkness (light degrades THC and terpenes)
- Airflow: Gentle air movement — not blowing directly on buds
  (blowing directly dries too fast and creates a hard outer shell
  while inside remains wet)
- Target: 10–14 days before buds are "snap dry" but stem doesn't fully
  break — it bends. This is the entry point for jars.

Curing (in jars, weeks 2–8+):
- Container: Glass jars, 1L or half-gallon mason jars
- Target RH inside jar: 58–62% (use Boveda 62% packs as reference)
- Burp schedule: Open jars 15–30 minutes twice daily for first week,
  then daily for week 2, then every few days as moisture stabilises
- Temperature: 16–21°C, dark storage
- Minimum cure: 3–4 weeks for significant terpene development
- Optimal cure: 6–8 weeks. After 8 weeks, improvements continue but
  slow dramatically.

The biochemistry of curing:
- Chlorophyll degrades (harsh green taste → smooth)
- Starch converts to sugar (smoothness)
- Terpene oxidation and conversion (flavour complexity)
- THCA converts very slowly to THC (minimal impact in 8 weeks)
- Moisture equalises throughout each bud (inside to outside)

### References
- Dr. Coco (cocoforcannabis.com). Environmental guides: VPD, humidity
  management, airflow. (Medium-agnostic content)
- Dimlux VPD charts (industry reference for commercial cannabis growers)
- Pulse grow room monitoring data — published aggregate data on
  real indoor grow environmental conditions
- Bugbee (2020) — CO2 and photosynthesis saturation (re: CO2 context)
"""


# =============================================================================
# Source 16 — Promix Technical Reference (Premier Tech Horticulture)
# Medium behavior, buffering, CEC, water retention for Promix HP / WP420
# =============================================================================
PROMIX_TECHNICAL_REFERENCE = """
## Promix and WP420 Technical Reference — Premier Tech Horticulture

### Source Overview
Premier Tech Horticulture (based in Rivière-du-Loup, Quebec) manufactures
Promix and licenses WP420 for the Canadian market. They publish free technical
guides for growers covering pH management, EC management, water retention,
and growing media performance.

Website: pthorticulture.com (grower resources section)
Note: WP420 is the licensed Canadian rebranding of what is functionally
equivalent to Promix HP (High Porosity). The specs differ slightly but
they behave identically for practical growing purposes.

### Product Comparison: Promix HP vs. WP420 vs. Promix BX

Promix HP (High Porosity):
- Composition: Canadian sphagnum peat (>75%), perlite, mycorrhizae
- Air porosity: 38–42% at container capacity (very high drainage)
- Water retention: Moderate-low
- CEC: ~12–15 meq/100g
- Starting pH: 5.5–6.5
- Best for: High-frequency watering, experienced growers, commercial

WP420 (Western Canada — distributed through Western Canada Growing):
- Essentially equivalent to Promix HP; same peat fraction, similar
  physical properties, marketed to Canadian growers
- Available in 3.8 and 12 cubic foot bales

Promix BX (Balanced Mix):
- More compost fraction vs. HP; slightly higher water retention
- Less drainage — not ideal for cannabis in high-frequency organic grows
  unless amending with extra perlite (30–40% by volume)

### Water Retention and Drainage Behaviour

Promix HP/WP420 has very high drainage capacity. At container capacity:
- ~40% air-filled pore space (excellent root zone oxygenation)
- ~30–35% water-filled pore space
- ~25–30% solid fraction (peat fibre)

This means:
1. You CANNOT overwater in a single event as easily as with soil,
   but you CAN keep roots wet by watering too frequently before
   the air pores refill with oxygen.
2. The medium becomes hydrophobic when it fully dries out —
   water beads and runs down the container edges rather than
   wetting the centre. Prevention: never let it fully dry;
   use yucca/wetting agent in the water if this occurs.
3. Runoff is your friend: 10–15% runoff at each watering ensures
   the entire medium is wetted and removes minor salt accumulation.

### pH Characteristics and Buffer Capacity

Peat pH dynamics:
- Fresh peat: pH 5.5–6.5 (peat is inherently acidic, hence the lime
  added by Premier Tech to raise to target range)
- Buffer capacity: LOW. Unlike agricultural soil with high CEC (20–40
  meq/100g), peat buffering is minimal. pH drifts more easily.

pH drift in organic grows:
- Organic amendment mineralisation produces organic acids → pH decreases
  over the grow cycle. Expect pH to drift 0.2–0.5 units downward over
  a full 12-week grow if amendments are the primary input.
- Counteracting: Periodic additions of dolomite lime (slow) or
  potassium bicarbonate in irrigation water (fast correction, temporary).

pH monitoring protocol:
- Check runoff pH every 2–3 weeks
- Target runoff pH: 6.0–6.5
- If runoff <5.8: Apply dolomite lime top dress; reduce any acidifying
  inputs temporarily
- If runoff >7.0: Water with slightly acidified water (pH 5.5–6.0)
  for 2–3 waterings to bring it down. Unusual in peat-based medium.

### EC (Electrical Conductivity) in Organic Promix Grows

EC measures ionic (salt) concentration in solution. In organic grows
with dry amendments, EC in the pore solution is largely from:
1. Mineral constituents of the amendments that dissolve immediately
2. Water mineral content (Vancouver water is very soft — low EC ~0.05)
3. Mineralised organic N in ammonium form (NH₄⁺ is ionic)

Typical runoff EC in organic Promix grows:
- After initial mix: 0.5–1.5 mS/cm (depends heavily on amendment rate)
- Steady state in active grow: 0.5–1.2 mS/cm
- If EC rises above 2.5 mS/cm: Salt accumulation — flush with plain water
- If EC drops below 0.3 mS/cm in mid-grow: Medium may be depleted;
  consider a top dress of amendments or a fish/kelp supplemental water

Note: The EC/ppm monitoring that's critical in synthetic grows is less
central in organic grows, because the nutrients are not ionic until
biology releases them. Runoff EC is a useful diagnostic but not the
primary management tool.

### Biological Considerations in Peat Media

Promix HP comes pre-inoculated with Premier Tech's own mycorrhizal
inoculant (Glomus intraradices). However:
- The inoculant has limited shelf life and variable viability depending
  on storage conditions
- A fresh application of mycorrhizal inoculant at transplant is still
  recommended for best results
- Promix has no other meaningful biological component — bacteria,
  fungi, protozoa, and nematode populations must be established
  through grower inputs (compost, compost tea, inoculants)

Biochar addition:
Adding 5–10% biochar by volume to Promix can improve CEC from ~12
to ~18–25 meq/100g (pre-charged biochar; activated by composting with
worm castings). This is a one-time upgrade that pays off over multiple
runs. Do not use fresh unactivated biochar — it adsorbs nutrients
aggressively at first and can temporarily cause deficiencies.

### Aeration Amendment Options

Perlite vs. Pumice vs. Rice Hulls (all used to add drainage to Promix
BX or to Coot's Mix recipes):

Perlite (standard): Glass-based volcanic mineral. Good drainage, no
CEC. Lightweight. Environmental concern: mining and processing is
energy-intensive. Floats to surface over time in containers.

Pumice: Volcanic rock. Better CEC than perlite (~5 meq/100g), doesn't
float, more stable over time. Preferred by living soil growers.
Available in BC from Pacific Northwest sources.

Rice Hulls: Organic, biodegradable. Good initial drainage but
decomposes over 3–6 months, reducing porosity. Good for one-time
grows; less ideal for no-till where you're reusing medium.

For Tim's Promix-based setup: Promix HP already has adequate
perlite fraction — additional aeration is not needed unless
supplementing with compost (which reduces porosity). If transitioning
toward a Coot's Mix-style recipe, substitute pumice or perlite at
25–35% by volume.

### Wettability and Anti-Hydrophobic Strategies

Peat becomes hydrophobic when dry. Prevention and correction:
1. Never let the medium completely dry — always maintain at least
   minimal moisture (3/10 on the moisture scale)
2. Use a wetting agent in irrigation water:
   - Yucca extract (ThermX-70, Thrive Alive): most commonly used,
     also feeds soil biology as a saponin source
   - Aloe vera (saponins): Can substitute; also adds growth hormones
   - Rate: 0.5–1 mL/L of irrigation water
3. If hydrophobia has already occurred: Soak the entire container
   in a bucket/tub of pH-adjusted water for 30–60 minutes to
   force re-wetting. Add a drop of wetting agent to the soak water.

### References
- Premier Tech Horticulture. Promix product data sheets, pH management
  guides, and grower resources. pthorticulture.com
- Premier Tech. (2019). Choosing and Managing Promix Growing Media.
  Technical guide for ornamental and vegetable producers.
- National standard pH and EC management data for peat-based media
  (compiled from multiple North American horticultural extension sources)
"""


# =============================================================================
# Ingestion functions
# =============================================================================

def _register_source(
    ks: KnowledgeStore,
    source_id: int,
    name: str,
    author: str,
    source_type: str,
    domain_tags: list,
    confidence: str,
    url: str | None,
    notes: str,
) -> bool:
    """Register a source row (INSERT if not exists, skip if already present)."""
    import psycopg2
    conn = ks._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM knowledge_sources WHERE id = %s", (source_id,))
            if cur.fetchone():
                logger.info("Source %d already registered — skipping INSERT", source_id)
                conn.commit()
                return False
            cur.execute(
                """
                INSERT INTO knowledge_sources
                    (id, name, author, source_type, domain_tags,
                     base_confidence, url, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (source_id, name, author, source_type,
                 domain_tags, confidence, url, notes),
            )
            conn.commit()
            logger.info("Registered source %d: %s", source_id, name)
            return True
    finally:
        ks._conn_params  # keep reference; connection auto-close on __del__
        conn.close()


def seed_gaia_green(ks: KnowledgeStore) -> int:
    """Seed Gaia Green + Promix reference (source_id=11)."""
    _register_source(
        ks, 11,
        name="Gaia Green Amendments in Promix/Peat Media — Tim's Setup",
        author="Gaia Green Organics / Premier Tech / Community Synthesis",
        source_type="reference_data",
        domain_tags=["gaia_green", "amendments", "promix", "wp420", "peat",
                     "4-4-4", "2-8-4", "organic", "feeding_schedule",
                     "top_dress", "ph", "watering", "cannabis"],
        confidence="high",
        url="https://gaiagreen.ca",
        notes=(
            "Tim-specific organic amendment reference: Gaia Green 4-4-4 and 2-8-4 "
            "in WP420/Promix HP peat medium. Covers feeding rates, schedule, "
            "pH management, biological inoculants, and common issues. "
            "Synthesised from Gaia Green product guides, Premier Tech data sheets, "
            "and applied academic findings (Caplan, Westmoreland/Bugbee)."
        ),
    )
    return ks.ingest_source(
        source_id=11,
        text=GAIA_GREEN_PROMIX_REFERENCE,
        topic_tags=["gaia_green", "amendments", "promix", "wp420", "4-4-4",
                    "2-8-4", "organic", "feeding_schedule", "top_dress",
                    "ph", "watering", "biology", "cannabis"],
        chapter="Gaia Green + Promix Reference Guide",
    )


def seed_buildasoil(ks: KnowledgeStore) -> int:
    """Seed Buildasoil / Coot's Mix reference (source_id=12)."""
    _register_source(
        ks, 12,
        name="Buildasoil / Coot's Mix — Living Soil Container Principles",
        author="Jeremy Silva (Buildasoil) / Clackamas Coot",
        source_type="guide",
        domain_tags=["living_soil", "coots_mix", "container", "buildasoil",
                     "top_dress", "watering", "organic", "amendments",
                     "no_till", "ipm"],
        confidence="high",
        url="https://buildasoil.com/blogs/news",
        notes=(
            "Jeremy Silva's Buildasoil framework: Coot's Mix foundational recipe, "
            "soil-from-scratch approaches (Recipe #1/2/3), watering principles for "
            "living soil containers, top dressing schedule, supplemental inputs "
            "(aloe, yucca, AACT), and organic IPM. "
            "Primary source for Tim's transition path from Promix+Gaia Green to "
            "true living soil. Published free on buildasoil.com."
        ),
    )
    return ks.ingest_source(
        source_id=12,
        text=BUILDASOIL_LIVING_SOIL_REFERENCE,
        topic_tags=["living_soil", "coots_mix", "container", "amendments",
                    "no_till", "watering", "top_dress", "compost_tea",
                    "ipm", "organic", "recipe"],
        chapter="Buildasoil Living Soil — Container Growing",
    )


def seed_kis_organics(ks: KnowledgeStore) -> int:
    """Seed KIS Organics / PNW organic cannabis reference (source_id=13)."""
    _register_source(
        ks, 13,
        name="KIS Organics — Pacific Northwest Organic Cannabis (Tad Hussey)",
        author="Tad Hussey (KIS Organics)",
        source_type="guide",
        domain_tags=["organic", "pnw", "bc", "water_only", "soil_reuse",
                     "no_till", "compost_tea", "cec", "biology",
                     "mycorrhizae", "vancouver", "water_quality"],
        confidence="high",
        url="https://kisorganics.com",
        notes=(
            "Tad Hussey's KIS Organics framework: water-only soil mix philosophy, "
            "re-amendment between runs, no-till container growing, AACT protocols, "
            "biological fertility principles, CEC building over time. "
            "PNW-specific context (Washington/BC maritime climate). Includes "
            "Vancouver water quality notes (soft glacial water, chloramine treatment). "
            "Podcast: 'Cannabis Cultivation and Science' (free, Spotify/Apple Podcasts)."
        ),
    )
    return ks.ingest_source(
        source_id=13,
        text=KIS_ORGANICS_REFERENCE,
        topic_tags=["organic", "pnw", "bc", "water_only", "soil_reuse",
                    "no_till", "compost_tea", "cec", "biology",
                    "mycorrhizae", "vancouver", "water_quality", "amendments"],
        chapter="KIS Organics — PNW Organic Cannabis",
    )


def seed_harley_smith(ks: KnowledgeStore) -> int:
    """Seed Harley Smith / NPK University nutrient chemistry (source_id=14)."""
    _register_source(
        ks, 14,
        name="Harley Smith / NPK University — Plant Nutrition Chemistry",
        author="Harley Smith",
        source_type="lecture",
        domain_tags=["nutrients", "cec", "ph", "cation_balance", "organic_acids",
                     "nitrogen_cycle", "terpenes", "cannabinoids",
                     "mineral_nutrition", "fulvic_acid", "humic_acid"],
        confidence="high",
        url="https://www.youtube.com/results?search_query=npk+university+harley+smith",
        notes=(
            "Harley Smith's NPK University lecture series (free, YouTube): "
            "plant nutrition science explained for cannabis growers. "
            "Covers CEC, cation competition (Ca/Mg/K/Fe interactions), "
            "pH and nutrient availability windows, N mineralisation timeline, "
            "organic acids and chelation, secondary metabolite production "
            "(terpenes, cannabinoids). Deepest nutrient chemistry reference "
            "in the knowledge base."
        ),
    )
    return ks.ingest_source(
        source_id=14,
        text=HARLEY_SMITH_REFERENCE,
        topic_tags=["nutrients", "cec", "ph", "cation_balance", "calcium",
                    "magnesium", "nitrogen", "organic_acids", "terpenes",
                    "cannabinoids", "mineralisation", "fulvic_acid"],
        chapter="Harley Smith — Nutrient Chemistry",
    )


def seed_environmental_management(ks: KnowledgeStore) -> int:
    """Seed environmental management reference (source_id=15)."""
    _register_source(
        ks, 15,
        name="Indoor Environmental Management — VPD, Humidity, Temperature, Airflow",
        author="Dr. Coco (cocoforcannabis.com) / Compiled Reference",
        source_type="reference_data",
        domain_tags=["vpd", "humidity", "temperature", "airflow", "environment",
                     "drying", "curing", "co2", "lights_off", "botrytis",
                     "2x2", "small_tent", "fan"],
        confidence="high",
        url="https://cocoforcannabis.com",
        notes=(
            "Comprehensive indoor environmental management reference for Tim's 2x2 tent. "
            "Based on Dr. Coco (cocoforcannabis.com) framework — medium-agnostic, "
            "directly applicable to any indoor grow. Covers: VPD tables and targets "
            "by stage, 2x2-specific challenges, temperature management (GrowHub context), "
            "humidity management through BC seasons, airflow and oscillating fans, "
            "CO2 considerations, lights-off Botrytis risk protocol, and full "
            "drying/curing environment guide."
        ),
    )
    return ks.ingest_source(
        source_id=15,
        text=ENVIRONMENTAL_MANAGEMENT_REFERENCE,
        topic_tags=["vpd", "humidity", "temperature", "airflow", "drying",
                    "curing", "co2", "lights_off", "2x2", "small_tent",
                    "botrytis", "environment", "fan", "growhub"],
        chapter="Environmental Management — 2x2 Indoor",
    )


def seed_promix_technical(ks: KnowledgeStore) -> int:
    """Seed Promix technical reference (source_id=16)."""
    _register_source(
        ks, 16,
        name="Promix / WP420 Technical Reference — Premier Tech Horticulture",
        author="Premier Tech Horticulture",
        source_type="reference_data",
        domain_tags=["promix", "wp420", "peat", "cec", "ph", "ec", "drainage",
                     "aeration", "hydrophobic", "wetting_agent", "biochar",
                     "perlite", "pumice"],
        confidence="high",
        url="https://www.pthorticulture.com/en/training-center/",
        notes=(
            "Premier Tech Horticulture technical data for Promix HP and WP420: "
            "CEC values (~12–15 meq/100g), air porosity (38–42%), pH dynamics "
            "in organic grows, EC management, hydrophobicity prevention, "
            "biological considerations (pre-inoculated mycorrhizae, building CEC). "
            "Covers perlite vs. pumice vs. rice hulls for aeration amendment "
            "and biochar addition for CEC improvement."
        ),
    )
    return ks.ingest_source(
        source_id=16,
        text=PROMIX_TECHNICAL_REFERENCE,
        topic_tags=["promix", "wp420", "peat", "cec", "ph", "ec",
                    "drainage", "aeration", "hydrophobic", "wetting_agent",
                    "biochar", "watering", "organic"],
        chapter="Promix / WP420 Technical Reference",
    )


# =============================================================================
# CLI
# =============================================================================

SOURCES = {
    "gaia_green": ("Gaia Green + Promix", seed_gaia_green),
    "buildasoil": ("Buildasoil / Coot's Mix", seed_buildasoil),
    "kis_organics": ("KIS Organics / PNW Organic", seed_kis_organics),
    "harley_smith": ("Harley Smith / NPK University", seed_harley_smith),
    "environment": ("Environmental Management (Dr. Coco)", seed_environmental_management),
    "promix": ("Promix Technical Reference", seed_promix_technical),
}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Seed organic cultivation knowledge into Jack's knowledge base"
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        help="Seed a specific source only",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available sources",
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable organic knowledge sources:")
        for key, (name, _) in SOURCES.items():
            print(f"  {key:<20} {name}")
        return

    cfg = load_jack_config()
    embedder = EmbeddingProvider(cfg)
    db_cfg = cfg.get("database", {})
    ks = KnowledgeStore(db_cfg, embedder)

    if args.source:
        name, fn = SOURCES[args.source]
        print(f"\nSeeding: {name}")
        n = fn(ks)
        print(f"  → {n} chunks created")
    else:
        print("\nSeeding all organic knowledge sources...")
        total = 0
        for key, (name, fn) in SOURCES.items():
            print(f"\n[{key}] {name}")
            n = fn(ks)
            print(f"  → {n} chunks created")
            total += n
        print(f"\nDone. Total chunks created: {total}")


if __name__ == "__main__":
    main()
