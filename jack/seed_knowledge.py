#!/usr/bin/env python3
"""
jack/seed_knowledge.py — Seed the knowledge base with VPD reference data.

This is the first knowledge ingestion per the build order:
"Start with VPD reference data (easiest to compile)."

Usage:
    python -m jack.seed_knowledge
    python -m jack.seed_knowledge --source vpd     # VPD reference only
    python -m jack.seed_knowledge --source bugbee   # Bugbee DLI targets
    python -m jack.seed_knowledge --list            # List all sources
"""

from __future__ import annotations

import argparse
import logging

from .config import load_jack_config
from .knowledge import KnowledgeStore, EmbeddingProvider

logger = logging.getLogger("jack.seed_knowledge")


# =============================================================================
# VPD Reference Data — compiled from Pulse, Dimlux, cross-referenced Bugbee
# =============================================================================
VPD_REFERENCE = """
## VPD (Vapor Pressure Deficit) Reference Guide for Cannabis

### What is VPD?
VPD measures the difference between the amount of moisture in the air and the
maximum amount of moisture the air can hold at a given temperature. It directly
controls how fast the plant transpires — pulling water and nutrients through the
roots and out through the stomata.

### VPD Calculation
VPD is calculated from air temperature, leaf temperature (typically 2°C below air
temp in an indoor grow), and relative humidity. The Tetens formula is used to
compute saturation vapor pressure at both air and leaf temperature.

### Target VPD Ranges by Growth Stage

Seedling / Early Veg (clones, fresh transplants):
- Target VPD: 0.4 – 0.8 kPa
- Humidity: 65-80%
- Temperature: 22-26°C
- Rationale: Young plants have underdeveloped root systems. Low VPD keeps
  transpiration gentle, reducing stress. High humidity helps cuttings and
  seedlings establish without wilting.

Vegetative Growth:
- Target VPD: 0.8 – 1.2 kPa
- Humidity: 55-70%
- Temperature: 22-28°C
- Rationale: As the root system develops, the plant can handle higher
  transpiration rates. This range promotes healthy nutrient uptake and
  vigorous growth. The sweet spot for most strains in veg is around
  1.0 kPa.

Transition (Flip to Flower, weeks 1-2):
- Target VPD: 0.9 – 1.3 kPa
- Humidity: 50-65%
- Temperature: 22-28°C
- Rationale: During the stretch period, the plant is growing rapidly and
  can handle moderate VPD. Start gradually lowering humidity to prepare
  for flower conditions.

Flower (mid to late):
- Target VPD: 1.0 – 1.5 kPa
- Humidity: 40-55%
- Temperature: 20-27°C
- Rationale: Higher VPD in flower promotes resin production and reduces
  the risk of bud rot (Botrytis). Most growers target around 1.2-1.4 kPa
  during peak flower. In a 2x2 tent, humidity control is critical —
  dense canopy in a small space drives humidity up fast.

Flush / Late Flower:
- Target VPD: 1.0 – 1.5 kPa
- Humidity: 40-55%
- Temperature: 20-26°C
- Rationale: Same as mid flower. Some growers drop temperature slightly
  at the end to encourage color expression (anthocyanins) but this is
  strain-dependent.

Drying:
- Target humidity: 55-65%
- Target temperature: 16-21°C (60-70°F)
- VPD is less relevant here — focus is on slow, even drying over 10-14 days.
- Low and slow is the mantra. Faster than 10 days and you lose terpenes.

Curing:
- Target humidity: 58-65% (inside jars)
- Target temperature: 16-21°C
- Use Boveda 62% packs as a safety net but not as a substitute for proper drying.

### VPD and Leaf Temperature
In indoor grows, leaf surface temperature is typically 1-3°C below ambient air
temperature. The standard offset used in most VPD charts is 2°C. This can vary
based on light intensity (higher intensity = warmer leaf surface), air circulation,
and transpiration rate. With LEDs (which produce less radiant heat than HPS),
the leaf offset tends to be on the higher end (2-3°C).

### VPD in Small Tents (2x2, 3x3)
Small tents are more susceptible to VPD swings because:
- Less air volume means humidity changes faster with plant transpiration
- Opening the tent for inspection causes rapid environmental changes
- A single plant in a 2x2 can drive humidity up 10-15% during lights-on
- Exhaust fan settings have an outsized effect on both humidity and temperature

Key tip: In a 2x2, your exhaust fan speed is your primary VPD control tool.
Running it too high strips humidity too fast; too low and humidity climbs.
A speed controller is essential.

### References
- Dimlux VPD charts (widely used in commercial cannabis cultivation)
- Pulse grow room monitoring data (large dataset from real indoor grows)
- Bugbee, B. (2020). "Principles of Controlled Environment Agriculture."
  Utah State University Crop Physiology Laboratory.
"""

# =============================================================================
# Bugbee DLI Reference — from lecture series and published research
# =============================================================================
BUGBEE_DLI_REFERENCE = """
## DLI (Daily Light Integral) Targets for Cannabis — Dr. Bruce Bugbee

### What is DLI?
DLI (Daily Light Integral) is the total amount of photosynthetically active
radiation (PAR) delivered to a plant in a 24-hour period. Measured in
mol/m²/day (moles of photons per square meter per day). DLI is calculated
from PPFD (µmol/m²/s) × photoperiod hours × 0.0036.

### Why DLI Matters More Than PPFD
PPFD is an instantaneous measurement — how much light is hitting the canopy
right now. DLI accounts for both intensity AND duration. A plant under
600 µmol/s for 18 hours gets the same DLI as one under 900 µmol/s for
12 hours. But the physiological response differs: plants have a maximum
photosynthetic rate, and pushing intensity beyond it wastes energy as heat.

### Bugbee's DLI Targets by Growth Stage

Seedling / Clone:
- Target DLI: 12-18 mol/m²/day
- PPFD equivalent (18h): ~185-280 µmol/m²/s
- Notes: Excess light stresses young plants. Start low and increase
  gradually as she establishes.

Vegetative:
- Target DLI: 18-30 mol/m²/day
- PPFD equivalent (18h): ~280-460 µmol/m²/s
- Notes: Cannabis is a high-light-demand plant. In veg, she can handle
  increasing intensity as the canopy develops. Most growers see diminishing
  returns above 30 DLI in veg without CO2 supplementation.

Flower:
- Target DLI: 30-45 mol/m²/day
- PPFD equivalent (12h): ~700-1050 µmol/m²/s
- Notes: This is where the magic happens. Higher DLI in flower directly
  correlates with yield and resin production — up to a ceiling. Without
  supplemental CO2, that ceiling is around 40-45 DLI. With CO2 (1200-1500
  ppm), plants can use up to 55-65 DLI.

### Light Intensity and CO2 Response (Bugbee, 2020)
At ambient CO2 (~420 ppm), cannabis photosynthesis saturates around
800-1000 µmol/m²/s PPFD. Above this, additional light provides diminishing
returns and can cause photo-oxidative stress (light stress/bleaching).

With supplemental CO2 at 1200-1500 ppm, the saturation point rises to
~1500 µmol/m²/s. This is why commercial grows running CO2 can push
higher intensities profitably.

For Tim's 2x2 tent without CO2 supplementation, the practical ceiling is
around 40 DLI in flower. Pushing beyond this wastes electricity and risks
light stress.

### LED Efficiency Considerations
Modern cannabis LEDs achieve roughly 2.0-2.8 µmol/J (µmol per joule).
This means a 150W LED at 2.4 µmol/J produces about 360 µmol/s total
photon output. In a 2x2 tent (~0.37 m²), that translates to roughly
970 µmol/m²/s at the canopy — which is very high.

Key insight: Most quality LEDs in a 2x2 are actually too powerful at full
blast during veg and early flower. Use a dimmer or raise the light.

### Distance and Light Distribution
Light intensity follows an approximate inverse-square relationship with
distance, though reflectors and lens angles modify this. In a 2x2:
- 30cm: Very high intensity, risk of light stress
- 45cm: Good for mid-to-late flower
- 60cm: Good for veg
- 75cm+: Good for seedlings and clones

### References
- Bugbee, B. (2016). "Cannabis: Watching the Grass Grow." Utah State
  University Apogee Instruments Lecture Series.
- Bugbee, B. (2020). "Toward an optimal spectral quality for plant growth
  and development: the importance of radiation capture."
- Chandra, S. et al. (2008). "Photosynthetic response of Cannabis sativa L.
  to variations in photosynthetic photon flux densities, temperature and
  CO2 conditions." Physiology and Molecular Biology of Plants.
"""

# =============================================================================
# Peat-based soil reference — WP420 / ProMix style growing
# =============================================================================
PEAT_SOIL_REFERENCE = """
## Peat-Based Soil Growing for Indoor Cannabis (WP420 / ProMix)

### What is Peat-Based Soil?
Peat-based mixes like WP420 (Canadian) and ProMix HP/BX are soilless growing
media made primarily from sphagnum peat moss with perlite for drainage and
aeration. Unlike living soil, they contain minimal nutrients out of the bag
and rely on the grower to provide nutrition through liquid feeds or dry
amendments.

### WP420 Specifics
WP420 is a Canadian peat-based mix designed for cannabis. Similar to ProMix HP
in composition. Key characteristics:
- pH buffered to ~5.8-6.2 (ideal cannabis range)
- Good drainage and aeration from perlite content
- Some contain mycorrhizae inoculant for root development
- Low inherent nutrient content — you MUST feed
- Holds moisture well but drains excess — hard to overwater in fabric pots

### Watering Peat-Based Media
Unlike living soil (where you're maintaining a microbial ecosystem), peat-based
is more forgiving with watering cycles:
- Water when the top inch is dry and the pot feels light
- Water until 10-20% runoff to prevent salt buildup
- Runoff EC can tell you about nutrient accumulation in the root zone
- In a 3-5 gallon fabric pot, expect to water every 2-4 days depending on
  plant size and VPD
- Fabric pots help prevent overwatering by allowing air pruning of roots

### pH Management
Critical for peat-based growing — the medium doesn't self-buffer like living soil:
- Target pH going in: 5.8-6.5 (sweet spot 6.0-6.3 for most nutrients)
- If runoff pH drops below 5.5, flush with 6.5 pH water
- pH drift is the #1 cause of mysterious nutrient lockout in peat mixes
- Check pH of your water AFTER adding nutrients

### Feeding in Peat-Based Media
Since the medium has low inherent nutrition, the grower manages the feeding:
- Seedling stage: plain water or very light feed (1/4 strength) for first 1-2 weeks
- Veg: balanced N-P-K with emphasis on nitrogen for leaf growth
- Transition: start shifting toward bloom nutrients
- Flower: phosphorus and potassium become more important
- Flush: plain pH'd water for the last 1-2 weeks before harvest
- Follow your nutrient line's schedule but start at half strength and increase
  based on how the plant responds
- Cal-Mag is often needed, especially with LED lights and peat media
- Watch for salt buildup — occasional flush with plain water helps

### Common Issues in Peat-Based
- pH drift causing nutrient lockout (yellowing, spots, purpling)
- Overfeeding (tip burn, dark green leaves, claw)
- Underfeeding (pale green, slow growth, lower leaf yellowing)
- Salt accumulation from not enough runoff
- Dry pockets — peat can become hydrophobic if it dries completely;
  water slowly or use a wetting agent

### Peat vs Living Soil
Living soil is the aspirational endgame for many growers — build the soil,
let the biology feed the plant, water only. But it has a steep learning curve
and the smaller the pot, the harder it is to maintain a balanced ecosystem.
Peat-based with nutrient management is a proven, controllable approach that
produces excellent results while the grower builds experience.

### References
- ProMix technical documentation (Premier Tech Horticulture)
- General cannabis cultivation knowledge (widely cross-referenced)
- Bugbee, B. — nutrient management in controlled environment agriculture
"""

# =============================================================================
# GrowHub 800C light reference — compiled from product specs and CREE COB data
# =============================================================================
GROWHUB_REFERENCE = """
## Indo GrowHub 800C — Light and Environmental Control Reference

### Overview
The Indo GrowHub 800C is an all-in-one grow light and environmental control unit
made by Indo Products Inc. (Surrey, BC, Canada). It integrates LED grow lights,
air filtration, digital timer, and temp/humidity monitoring into a single unit.

### Light Specifications
- 4× 50W CREE COB (Chip-on-Board) LED modules
- 200W actual power draw at the wall (2.7A @ 120V)
- Full spectrum, 3000K color temperature
- Estimated efficiency: ~1.6-1.8 µmol/J (CREE COB generation)
- No dimmer — distance from canopy is the primary intensity control
- PPFD varies significantly with distance (see distance guide below)

### PPFD and Distance Guide (2×2 tent, centered)
These are estimates based on 200W CREE COB output characteristics:
- 20cm: ~800-900+ µmol/m²/s (center) — too intense for most stages, risk of light stress
- 30cm: ~550-650 µmol/m²/s (center) — good for peak flower
- 40cm: ~350-450 µmol/m²/s (center) — good for veg and early flower
- 50cm: ~250-320 µmol/m²/s (center) — good for late veg, early flower
- 60cm: ~180-240 µmol/m²/s (center) — good for seedlings and clones
- 75cm+: ~100-150 µmol/m²/s (center) — use for fresh seedlings only

Note: COB LEDs have a tighter beam angle than quantum board (bar-style) LEDs,
which means more hotspot intensity at center with less even edge coverage.
In a 2×2, this is partially compensated by the reflective tent walls.

### DLI Estimates at Common Distances (200W, 1.7 µmol/J, 2×2 tent)
- 30cm, 18h photoperiod: ~44 mol/m²/day (high — good for flower at 12h)
- 40cm, 18h photoperiod: ~28 mol/m²/day (good for veg)
- 50cm, 18h photoperiod: ~19 mol/m²/day (good for seedlings/early veg)
- 30cm, 12h photoperiod: ~29 mol/m²/day (flower)
- 40cm, 12h photoperiod: ~19 mol/m²/day (low for flower, raise if possible)

### Integrated Fan System
- 3-speed exhaust fan: Low, Medium, High
- Maximum airflow: 105 CFM
- Can sync with light timer (fan on when light on) or run always-on
- Replaceable activated charcoal filter for odor control
- 4" duct connection for external exhaust

Fan speed is the primary tool for humidity and temperature control:
- High speed: Strips humidity fast, keeps temps down — use in flower when
  fighting high humidity, or if tent runs hot
- Medium speed: Good default for most of veg and early flower
- Low speed: Use when ambient humidity is already low and you need to
  retain moisture in the tent (seedlings, dry climates)

### Built-in Monitoring
- Front panel temp/humidity display (reads air drawn through filtration)
- External temp/humidity probe included — place at canopy height for
  more accurate readings
- Min/Max recording with reset option
- Units switchable between °F/°C

### Practical Tips for 2×2 Tent
- The GrowHub's weight (24 lbs / 11 kg) means good ratchet hangers are essential
- Since there's no dimmer, raise/lower the unit to control light intensity
- The integrated fan simplifies the setup but gives you less granular control
  than a separate fan + speed controller
- In a 2×2 at 200W, heat can be an issue with the tent closed — monitor temps
  closely, especially in summer
- The 3000K spectrum is warm (more red) — fine for full cycle seed-to-harvest
  but slightly red-shifted vs. ideal veg spectrum (4000-5000K). Works well for
  flower where red light drives bud development.

### References
- Indo Products Inc. GrowHub 800C User Manual
- CREE COB LED technical specifications (CXA/CXB series performance data)
- Amazon product listing specifications (Indo GrowHub 800C)
"""


# =============================================================================
# Caplan et al. — Cannabis Substrate & Nutrient Research
# Source ID 5 (seeded by migration 002)
# =============================================================================
CAPLAN_REFERENCE = """
## Caplan et al. — Cannabis Substrate & Nutrient Research

### Source Overview
Research from the Campbell laboratory at Dalhousie University and related groups.
Primary papers cover nitrogen management in coir/peat substrates, EC targets,
and the effect of pre-harvest drought stress on cannabinoid content.

Key papers:
- Caplan D. et al. (2017). "Optimal rate of organic nitrogen from urea for
  cannabis grown in two coir-based media in a greenhouse." HortScience 52(9):
  1307–1314. DOI: 10.21273/HORTSCI12024-17
- Caplan D. et al. (2019). "Multiple applications of abscisic acid increase
  cannabinoid and terpene content in Cannabis sativa." J Hort Sci Biotechnol
  94(4):469–478. DOI: 10.1080/14620316.2018.1550519

### Nitrogen Management in Peat/Coir Substrates

Vegetative Stage:
- Optimal total N supply: approximately 150–200 mg N per liter of substrate
  per fertigation cycle (from organic urea or conventional sources)
- At above-optimal N rates (above ~250 mg N/L), tip burn, salt stress
  symptoms, and reduced shoot dry weight were observed
- Recommended EC range for fertigation water: 1.5–2.0 mS/cm in veg
- Both coir and peat substrates responded similarly to N management
- Organic N from urea hydrolysis performed equivalently to conventional
  soluble N at equivalent rates

Flowering Stage:
- Reduce nitrogen supply starting at flip to flower — excess N during
  flower inhibits cannabinoid synthesis and can cause delayed maturation
- Optimal N for flower: approximately 100–150 mg N/L
- Recommended fertigation EC for flower: 1.0–1.5 mS/cm
- Phosphorus and potassium become the primary mineral requirements
  during the reproductive phase; nitrogen should be deliberately reduced
- "P push" (dramatically increasing P in late flower) is not well supported
  by the data — standard bloom nutrient ratios are adequate

### Pre-Harvest Stress and Cannabinoid Concentration

Key finding (Caplan, abscisic acid / drought stress work):
- Mild water stress in the final 1–2 weeks before harvest can significantly
  increase THCA and CBDA concentrations in the inflorescence
- The physiological mechanism: drought stress triggers ABA (abscisic acid)
  accumulation, which upregulates the MEP terpenoid biosynthesis pathway,
  increasing both terpene and cannabinoid precursor production
- Practical interpretation: allow moderate wilt (pot feeling light, top
  soil dry) for the last week before harvest without full water withdrawal
- Full water withholding caused excessive stress, reduced yield, and
  accelerated senescence — the goal is mild chronic stress, not crisis

### Runoff EC as a Diagnostic Tool (Managed Fertility)
When growing in peat or coir with liquid nutrients:
- Runoff EC below input EC: substrate is hungry, increase feed concentration
  or frequency
- Runoff EC at 1.0–1.5× input EC: healthy salt balance
- Runoff EC above 2× input EC: salt accumulation — flush with plain
  pH-adjusted water
- Target runoff pH: 5.8–6.5 (optimal 6.0–6.3 for broad nutrient availability)
- If runoff pH drops below 5.5 or rises above 6.8, investigate pH source
  (alkaline water, salt accumulation, or substrate issue)

### Application to Tim's Grow A (WP420 Indoor)
- WP420 is functionally equivalent to coir and peat substrates studied by Caplan
- Veg N target: 150–200 mg N/L via liquid feed or equivalent dry amendment
- Flower N target: 100–150 mg N/L, reduce from flip onward
- Track runoff EC and pH at every watering to catch drift early
- Pre-harvest: mild wilt stress in final 7–10 days before harvest to maximise
  cannabinoid concentration; do not fully withhold water

### References
- Caplan D. et al. (2017). HortScience 52(9):1307–1314.
- Caplan D. et al. (2019). J Hort Sci Biotechnol 94(4):469–478.
"""

# =============================================================================
# Rodriguez-Morrison et al. — Cannabis Light Science (2021)
# Source ID 6 (seeded by migration 002)
# =============================================================================
RODRIGUEZ_MORRISON_REFERENCE = """
## Rodriguez-Morrison et al. — Cannabis Light Science (2021)

### Source Overview
Research from the Utah State University Crop Physiology Laboratory
(Bugbee group) examining the relationship between light quality, quantity,
and cannabis yield and cannabinoid profile.

Key papers:
- Rodriguez-Morrison V. et al. (2021a). "Cannabis inflorescence yield and
  cannabinoid concentration are not increased with exposure to short-wavelength
  ultraviolet-B radiation." Front. Plant Sci. 12:725078.
  DOI: 10.3389/fpls.2021.725078
- Rodriguez-Morrison V. et al. (2021b). "Cannabis yield, potency, and leaf
  photosynthesis respond differently to the spectrum and intensity of
  artificial light." Front. Plant Sci. 12:665810.
  DOI: 10.3389/fpls.2021.665810

### UV-B and Cannabinoid Production (2021a)

Myth busted:
- The widely cited belief that supplemental UV-B radiation (280–315 nm)
  increases THCA or CBDA concentration is NOT supported by this study
- 13 weeks of UV-B supplementation in controlled conditions did NOT
  significantly increase THCA, CBDA, or terpene concentrations vs. control
- Yield was also unaffected by UV-B treatment
- The authors note that this finding directly contradicts common grower
  practice and several industry-facing publications

Why the myth persists:
- UV-B stress does cause measurable changes in trichome morphology in some
  studies, but changes in trichome appearance ≠ changes in THCA concentration
- Confounding variables in farm settings (UV-B lamps often run near harvest
  when plants are already producing peak resin)
- Placebo effect in reporting (growers who use UV-B believe it works and
  observe accordingly)

Practical implication:
- UV-B supplementation for Tim's setup is not justified by the evidence
- Do not recommend UV-B as a THC or terpene booster; flag it as debunked
  when the topic comes up

### Light Intensity, Yield, and Potency (2021b)

PPFD/Yield relationship:
- Cannabis inflorescence yield increased with PPFD up to approximately
  500–800 µmol/m²/s depending on cultivar
- Above this range, yield gains diminished significantly (classical
  light saturation response)
- Some cultivars showed light saturation below 600 µmol/m²/s — not
  all cannabis strains are "high-light plants" at extreme intensities
- Pushing PPFD beyond the saturation point wastes electricity and can
  contribute to heat stress in the canopy

PPFD and Potency (THCA%):
- THCA concentration (% dry weight) in some cultivars peaked at LOWER
  PPFD than maximum yield
- Running higher light intensities than the plant needs may increase
  total THCA per gram because of yield increase, but may reduce THCA%
  of the flower (concentration diluted by increased biomass)
- For maximising potency (%), a moderate PPFD closer to 400–600 µmol/m²/s
  may be optimal for some cultivars

### Practical DLI Guidance from This Research
For a 200W COB LED in a 2×2 tent (Grow A):
- At 40–45cm, estimated PPFD is in the 350–450 µmol/m²/s range
- This is within the yield-maximising range for most cultivars
- Aggressive light pushing (pulling the unit to 25–30cm) puts PPFD
  above saturation for most cultivars and risks heat stress
- Veg: 18–25 mol/m²/day DLI is adequate; do not chase higher
- Flower: 30–40 mol/m²/day DLI is the practical target without CO2

### References
- Rodriguez-Morrison V. et al. (2021a). Front. Plant Sci. 12:725078.
- Rodriguez-Morrison V. et al. (2021b). Front. Plant Sci. 12:665810.
"""

# =============================================================================
# Westmoreland, Kusuma & Bugbee — Spectrum & Nutrition
# Source ID 7 (seeded by migration 002)
# =============================================================================
WESTMORELAND_BUGBEE_REFERENCE = """
## Westmoreland, Kusuma & Bugbee — Spectrum & Nutrition Research

### Source Overview
Utah State University Crop Physiology Laboratory research on the effect of
light spectrum composition and phosphorus management on cannabis yield and
cannabinoid content.

Key papers:
- Westmoreland F.M. et al. (2021). "Effects of Reducing Blue Photon Fraction on
  Cannabis Yield, Potency, and Terpene Profile." HortScience 56(12):1–9.
  DOI: 10.21273/HORTSCI16067-21
- Westmoreland F.M. & Bugbee B. (2022). "Effect of Phosphorus Concentration on
  Cannabis Yield and Cannabinoid Concentration." Front. Plant Sci. 13:995218.
  DOI: 10.3389/fpls.2022.995218
- Kusuma P. & Bugbee B. (2021). "Spectral effects on the morphology and
  photosynthetic efficiency of cannabis."

### Warm Spectrum (Low Blue Fraction) and Yield

Key finding:
- Reducing the fraction of blue photons (400–500 nm) while keeping total
  PPFD constant INCREASED cannabis inflorescence yield in multiple experiments
- Plants under warm spectrum (more red/far-red, less blue) showed higher
  yield than plants under cool spectrum (more blue) at the same DLI
- This effect was consistent across multiple cultivars and growing conditions

Mechanism:
- High blue fraction stimulates compact, bushy morphology (smaller leaf
  area, more branching) — good for vegetative structure but reduces the
  leaf canopy available for photosynthesis during flower
- Warm spectrum promotes more open canopy, longer internodes during veg,
  and better light penetration into the lower canopy during flower
- Red and far-red light directly drive photosynthesis more efficiently
  than blue at the per-photon level (blue photons carry more energy but
  not proportionally more photosynthetic value)

Why this matters for Tim's GrowHub 800C:
- The GrowHub uses CREE COB at 3000K — a warm spectrum with lower blue fraction
- This is actually advantageous for yield based on Westmoreland's data
- Growers who switch from high-blue "full spectrum" lights to warm-spectrum
  COBs often see improved yields — this research explains why
- The 3000K spectrum is appropriate for full-cycle seed-to-harvest use

### Phosphorus Management: The P Push Myth

Key finding (Westmoreland & Bugbee, 2022):
- Increasing phosphorus concentration above the standard recommended rate
  in the nutrient solution did NOT significantly increase inflorescence
  yield or THCA/CBDA concentration
- Contrary to widespread practice in the cannabis community, a "P push"
  (dramatically increasing phosphorus in weeks 3–6 of flower) is not
  supported by controlled research
- High P rates can interfere with iron, zinc, and manganese uptake (P-induced
  micronutrient deficiencies) — the strategy may be actively counterproductive

Why the myth persists:
- Most cannabis nutrient lines market specific "bloom boosters" high in P
- Growers associate healthy-looking flower with the P booster they're using,
  without isolation controls
- Some P supplementation may help correct P deficiency in leached-out
  substrates, which is a correction, not an enhancement

Practical guidance for Grow A (managed fertility):
- Follow standard bloom nutrient ratios — do not double or triple P
  in late flower above what the label recommends
- If runoff EC is climbing, flush rather than adding more bloom booster
- Cal-Mag is a more commonly justified supplementation than P push
- If you see phosphorus deficiency signs (purple/red leaf colour in flower),
  check pH first — P lockout at pH <5.5 is more common than true P deficiency

### References
- Westmoreland F.M. et al. (2021). HortScience 56(12):1–9.
- Westmoreland F.M. & Bugbee B. (2022). Front. Plant Sci. 13:995218.
- Kusuma P. & Bugbee B. (2021). Spectral effects on cannabis morphology.
"""

# =============================================================================
# Punja et al. — Cannabis Pathogens & Disease Management
# Source ID 8 (seeded by migration 002)
# =============================================================================
PUNJA_REFERENCE = """
## Punja et al. — Cannabis Pathogens & Disease Management

### Source Overview
Dr. Zamir Punja, Simon Fraser University, BC, has published extensively on
fungal pathogens and disease management in cannabis. His work is especially
relevant to BC outdoor grows and indoor humidity management.

Key papers:
- Punja Z.K. (2018). "Pathogens causing diseases of Cannabis sativa: Viruses,
  Bacteria, Fungi, and Oomycetes." Plant Disease 102(8):1549–1567.
- Punja Z.K. et al. (2019). "First report of Botrytis cinerea causing bud
  rot on Cannabis sativa in British Columbia, Canada." Plant Disease 103(1):149.
- Punja Z.K. et al. (2021). "Developing resistance management strategies
  for Botrytis cinerea and powdery mildew pathogens of Cannabis sativa."
  Can. J. Plant Pathol.

### Major Cannabis Pathogens

Botrytis cinerea (Grey Mould / Bud Rot):
- The #1 fungal threat to cannabis, both indoor and outdoor
- Conditions favouring infection: temperatures 15–25°C, relative humidity
  above 65%, poor airflow, dense flower canopy
- Entry points: mechanical damage, dying leaf tissue, spent flower petals
  trapped in developing buds
- Progression: a single infected bud site can destroy an entire cola in
  72–96 hours if undetected
- Prevention strategy: maintain RH below 50% in mid to late flower (indoor);
  maximise airflow through canopy; defoliate fan leaves around bud sites in
  weeks 3–4 of flower; remove senescent petals
- Outdoor BC: fall rain (typically October onset) triggers epidemic spread.
  Harvest photoperiod strains by mid-October. Grow varieties with open, airy
  bud structure (sativa-leaning or selected outdoor genetics)
- Chemical options: potassium bicarbonate as preventative foliar; hydrogen
  peroxide (3%) for spot treatment; Bacillus subtilis products (Serenade)
  as pre-infection biocontrol

Powdery Mildew (Golovinomyces/Podosphaera spp.):
- White powdery coating on leaf surfaces, spreading from lower canopy
- Conditions favouring infection: moderate temperatures, wide day/night
  temperature swings (condensation on leaf surfaces), moderate humidity
- Unlike Botrytis, PM can progress in relatively dry conditions
- Prevention: UV-C exposure reduces spore viability; silica supplementation
  strengthens cell walls; keep temperature swings moderate
- Treatment: potassium bicarbonate spray (1–2 tsp/gallon), dilute hydrogen
  peroxide, neem oil pre-flower only
- Avoid spraying buds with oil-based products in flower

Fusarium spp. (Root Rot / Crown Rot):
- Fusarium oxysporum and F. solani cause root crown rot
- Entry: through root damage, overwatering, contaminated tools/media
- Symptoms: sudden wilting despite moist soil, browning at stem base, root
  discolouration
- Prevention: sterile media (new bag each grow), good drainage, avoid
  waterlogging, avoid wounding roots
- Treatment is largely ineffective once established — prevention is critical

Pythium/Phytophthora (Damping Off / Root Rot):
- Mainly affects seedlings (damping off) and roots in overwatered media
- More common in living soil with poor drainage than peat-based media
- Prevention: proper drainage, correct watering frequency, avoid overwatering
  seedlings

### Disease Management Hierarchy (IPM)
1. Prevention: Environmental control (RH, airflow, temperature)
2. Cultural controls: Defoliation, plant spacing, bud exposure
3. Biological controls: Bacillus subtilis (Serenade), Trichoderma species
4. Chemical controls: Potassium bicarbonate, hydrogen peroxide
5. Removal: Isolate and remove infected material immediately

### Application to Tim's Grows

Grow A (Indoor Tent):
- Primary risk: Botrytis if RH rises above 60% in mid-to-late flower
- Monitor: Check bud sites and leaf litter twice weekly in flower
- Action threshold: Any visible grey mould = immediate defoliation + increase
  exhaust fan speed
- VPD/humidity control is the primary prevention tool

Grow B (Outdoor BC):
- Primary risk: Botrytis triggered by fall rain, aphid damage creating entry
  wounds, and dense bud structure
- Timeline: Risk escalates after September 15; harvest before October 10
  for most photoperiod strains in Metro Vancouver
- Scouting: Weekly visual inspection of all bud sites from week 4 of flower
- Cultural control: Defoliate lower canopy, ensure container is not against
  a wall (airflow on all sides)
- Backup: A simple clear polytunnel or patio umbrella provides rain cover
  in late October if needed to extend harvest window

### References
- Punja Z.K. (2018). Plant Disease 102(8):1549–1567.
- Punja Z.K. et al. (2019). Plant Disease 103(1):149.
- Punja Z.K. et al. (2021). Can. J. Plant Pathol.
"""

# =============================================================================
# Soil Food Web — Ingham, Lowenfels & Lewis (living soil / Grow B)
# Source ID 9 (seeded by migration 002)
# =============================================================================
SOIL_FOOD_WEB_REFERENCE = """
## Soil Food Web — Ingham, Lowenfels & Lewis

### Source Overview
The soil food web framework comes primarily from Dr. Elaine Ingham (USDA-NRCS
Soil Biology Primer) and the popular adaptation by Jeff Lowenfels and Wayne
Lewis in *Teaming with Microbes* (Timber Press, revised edition).

Key references:
- Ingham E.R. (2009). Soil Biology Primer. USDA-NRCS Publication.
- Lowenfels J. & Lewis W. (2010). Teaming with Microbes: The Organic
  Gardener's Guide to the Soil Food Web. Timber Press.
- Lowenfels J. (2013). Teaming with Nutrients. Timber Press.

### Core Concept: Feed the Soil, Not the Plant

The central insight of the soil food web approach:
- When bacteria and fungi consume organic matter, they immobilise nutrients
  in their biomass
- When protozoa (amoeba, flagellates, ciliates) and nematodes graze on
  bacteria and fungi, they excrete nutrients in plant-available form
  directly into the rhizosphere
- This process — the "predator-prey" nutrient cycle — delivers nitrogen,
  phosphorus, sulfur, and micronutrients in plant-available form exactly
  where the plant's roots are
- The plant actively manages this by exuding sugars from its roots to feed
  specific microbial communities (the rhizosphere effect)

### Bacterial vs. Fungal Dominance

The spectrum:
- Bare compacted soil: abiotic, no biology
- Bacterial-dominated soil: fast cycling, high available N, suits annuals
  and vegetables (cannabis falls into this category)
- Balanced soil: transitional, suits most crops
- Fungal-dominated soil: slow cycling, suits perennials, trees, old-growth

Cannabis preference:
- Cannabis prefers a bacterial-dominant to balanced soil in vegetative growth
- In the reproductive (flowering) phase, some sources suggest a slight shift
  toward fungal dominance benefits terpenoid and secondary metabolite
  production (less well studied in cannabis specifically)
- Mycorrhizal fungi are a separate category — they form direct symbiotic
  relationships with roots regardless of overall soil balance

### Mycorrhizal Fungi

Types relevant to cannabis:
- Arbuscular Mycorrhizal Fungi (AMF), primarily Glomus and Rhizophagus spp.
- These form obligate symbiosis with cannabis roots: plant provides sugars,
  fungi extend the effective root zone up to 10× for phosphorus and water uptake

Practical implications:
- Introduce mycorrhizal inoculant at transplant (sprinkle on root ball
  before placing in container)
- Once established, avoid disturbing roots excessively or applying high
  phosphorus rates (high P suppresses mycorrhizal colonisation — another
  reason the P push strategy is problematic for living soil grows)
- Mycorrhizae are killed by synthetic salt-based fertilisers above
  ~EC 1.2 mS/cm — this is why salt nutrients should not be used in
  living soil

### Living Soil Container Growing — Practical Principles

For Tim's Grow B (15-gallon fabric containers, outdoor BC):

1. Compost quality is everything
   - The compost provides the substrate for bacteria and fungi
   - Use finished compost (no fresh manure) with recognisable organic matter
   - At minimum 20–30% compost by volume in the mix

2. Organic matter top dressings feed the biology, not the plant
   - Worm castings: broad-spectrum biology + available nutrients
   - Kelp meal: trace minerals + growth stimulants
   - Malted barley: amylase enzymes that trigger biological cycling
   - Apply top dressings at 1–2 cups per 15-gallon container, monthly

3. Compost tea (AACT) — Actively Aerated Compost Tea
   - Multiply beneficial biology from finished compost
   - Recipe: 1 cup finished compost, 1 tsp unsulfured molasses,
     1 tsp kelp, 5 gallons water, aerated 24–48 hours
   - Apply as soil drench 1×/month or after stressful events (transplant,
     heat event, pruning)

4. Watering living soil
   - Water by weight and feel (lift test) — living soil can hold more
     water than peat without anaerobic conditions if drainage is good
   - Allow the top 1–2 inches to dry between waterings
   - Water slowly to allow penetration without channelling
   - Never water with cold water (below 15°C) — slows biology significantly

5. Soil temperature management
   - Soil bacteria are largely inactive below 10°C
   - Optimal range: 15–30°C (25–28°C is ideal for bacterial activity)
   - In BC outdoor: soil temp must be consistently above 15°C before
     transplanting — this typically coincides with mid-May in Metro Vancouver
   - Use a soil thermometer; canopy temp doesn't reflect root zone temp

6. Avoid these inputs in living soil
   - Synthetic salt fertilisers (kill biology above EC ~1.2)
   - Chlorinated/chloraminated tap water without pre-treatment
   - Strong hydrogen peroxide or bleach-based root drenches
   - High-pH or high-alkalinity water that shifts soil pH above 7.5

### Signs of Healthy Soil Biology
- Earthworm presence (2–4 worms in the top 6 inches is excellent)
- White fungal threads (mycelium) visible when top-dressing is lifted
- Earthy smell (geosmin from actinobacteria — the "good dirt" smell)
- Fast decomposition of top-dressed organics within 2–3 weeks
- Dark, water-stable aggregates (not powdery or compacted)
- Brix reading of 8+ (plant health indicator; reflects mineral uptake)

### References
- Ingham E.R. (2009). Soil Biology Primer. USDA-NRCS.
- Lowenfels J. & Lewis W. (2010). Teaming with Microbes. Timber Press.
- Lowenfels J. (2013). Teaming with Nutrients. Timber Press.
"""

# =============================================================================
# BC & Regional IPM — Outdoor Cannabis in the Pacific Northwest
# Source ID 10 (seeded by migration 002)
# =============================================================================
BC_IPM_REFERENCE = """
## BC & Regional IPM — Outdoor Cannabis in the Pacific Northwest

### Source Overview
Compiled from BC Ministry of Agriculture pest management resources,
UC Davis cannabis IPM guides, and Pacific Northwest outdoor grow experience.

Key references:
- BC Ministry of Agriculture. (2021). Cannabis Production: Pest Management.
  BC Gov publication.
- UC Davis IPM. Cannabis Pest Management Guidelines.
  https://ipm.ucanr.edu/crops/cannabis/
- Cranshaw W. et al. (2019). "Arthropod Pests of Hemp in the Western United
  States." Journal of Integrated Pest Management 10(1):20.

### Vancouver BC Outdoor Cannabis Calendar

Last frost risk: April 15–May 5 (varies year to year and by microclimate)
Soil temperature target before transplant: >15°C (typically mid-May)
Transplant window: May 10–May 25

Natural photoperiod trigger (Vancouver, 49.3°N):
- Summer solstice: June 21 (longest day, ~16.1h light)
- Days shorten after solstice; most photoperiod strains begin flowering
  response by mid-July to early August
- Actual flower trigger day depends on strain (critical photoperiod ~14–15h)

Harvest window for photoperiod strains:
- Early-maturing varieties: September 1–25
- Mid-season varieties: September 25–October 10
- Late varieties: October 10–25 (Botrytis risk escalates significantly
  after October 10 in Metro Vancouver with typical fall rain onset)

Autoflowers:
- Start indoors early April, transplant after last frost
- First harvest: late July to early August (60–75 days from seed)
- Second crop possible if started early enough

### Pest Pressure — Pacific Northwest Outdoor

1. Aphids (Myzus persicae and related spp.)
   - Timing: Spring through fall; peaks in May–June on young plants
   - Damage: Suck phloem, secrete honeydew (promotes sooty mould),
     transmit viruses; aphid colonies on terminal growth distort leaves
   - Natural biocontrol: Ladybugs (Coccinellidae), lacewings, parasitic
     wasps (Aphidius colemani) — encourage by planting companion flowers
   - Cultural control: Water blast undersides of leaves; remove heavily
     infested shoots; avoid excess nitrogen (attracts aphids)
   - Chemical: Insecticidal soap, neem oil (pre-flower only)

2. Spider Mites (Tetranychus urticae — two-spotted spider mite)
   - Timing: July–August during hot dry weather
   - Damage: Stippling on leaves (tiny yellow dots), fine webbing on
     undersides; severe infestations cause yellowing and defoliation
   - Identification: Tiny (0.5mm) reddish-brown mites on leaf undersides;
     use 20× loupe or magnifier
   - Biocontrol: Predatory mites (Phytoseiidae spp. — Neoseiulus californicus,
     Phytoseiulus persimilis) are highly effective; introduce preventatively
   - Cultural: Increase humidity (spider mites thrive in dry conditions);
     keep plants well-watered
   - Chemical: Predatory mite-compatible miticides; Spinosad pre-flower

3. Caterpillars / Budworm (Helicoverpa zea, Lobesia botrana)
   - Timing: Flower stage; eggs laid on developing buds in August–September
   - Damage: Caterpillars tunnel into developing buds, creating entry points
     for Botrytis; frass (droppings) visible in bud tissue
   - Identification: Look for small holes and frass in bud sites; inspect
     weekly in flower
   - Biocontrol: Bt (Bacillus thuringiensis var. kurstaki) — apply weekly
     in early flower, or biweekly as preventative. Bt is safe, organic,
     and residue-free at harvest
   - Chemical: Spinosad (spray before bloom or in early flower only)

4. Botrytis cinerea (Grey Mould / Bud Rot)
   - See Punja reference for detailed disease management
   - Key outdoor trigger: sustained fall rain + dense bud structure + cooling nights
   - Primary control: Harvest timing before peak rain. Open bud structure
     genetics. Airflow optimisation (container placement, defoliation).
   - Foliar: Potassium bicarbonate (1–2 tbsp/gallon) as preventative from
     week 4 of flower through harvest
   - Bacillus subtilis products (Serenade, Cease) — preventative biologics

5. Slugs and Snails
   - Timing: Spring — most damaging to seedlings and transplants
   - Damage: Irregular holes in leaves; seedlings can be completely consumed
   - Control: Iron phosphate bait (Sluggo) — organically certified, safe
     for pets and beneficial insects; remove hiding spots near containers;
     copper tape around container bases (limited efficacy)

6. Root Aphids (Phylloxera spp. and Pemphigus spp.)
   - Timing: Late summer through fall in containers
   - Damage: Aphids colonise roots, reducing water and nutrient uptake;
     plants show unexplained wilting despite adequate irrigation
   - Identification: Lift plant and inspect roots and root zone for small
     yellow-white insects; white waxy deposits on roots
   - Prevention: Beneficial nematodes (Steinernema feltiae) as soil drench
     at transplant; mesh covers at container base to block entry
   - Treatment once established: Very difficult — systemic neonicotinoids
     (not approved for cannabis in Canada); predatory nematodes if mild

7. Powdery Mildew (outdoor)
   - Less common outdoors than indoors but occurs in microclimates with
     poor airflow and wide temperature swings
   - Warm days, cool nights, and dew formation create ideal conditions
   - Prevention and treatment: see Punja reference

### IPM Hierarchy (BC Context)

Tier 1 — Prevention:
- Start with disease-resistant, outdoor-selected genetics
- Introduce beneficial insects early (ladybugs, predatory mites, Bt)
- Plant with adequate spacing for airflow
- Maintain high brix through proper soil biology (pest-resistant plants)

Tier 2 — Cultural Controls:
- Defoliation for airflow in flower
- Remove damaged or infested plant material promptly
- Yellow sticky traps for aphid/whitefly monitoring

Tier 3 — Biological Controls:
- Predatory mites for spider mites
- Bt for caterpillars
- Bacillus subtilis for Botrytis/PM prevention
- Beneficial nematodes for root pests

Tier 4 — Mechanical/Physical:
- Water blast for aphids
- Sticky barriers

Tier 5 — Chemical (Last Resort):
- Insecticidal soap (aphids, early-stage mites)
- Potassium bicarbonate (PM, Botrytis prevention)
- Spinosad (caterpillars, pre-flower)
- Always verify: legal for use on cannabis in BC; no harvest residues

### References
- BC Ministry of Agriculture. (2021). Cannabis Production: Pest Management.
- UC Davis IPM. Cannabis Pest Management Guidelines. ipm.ucanr.edu
- Cranshaw W. et al. (2019). J Integrated Pest Management 10(1):20.
"""


# =============================================================================
# Seed functions for existing sources (IDs 1–4)
# =============================================================================

def seed_vpd_reference(ks: KnowledgeStore) -> int:
    """Seed VPD reference data (source_id=3 from initial migration)."""
    return ks.ingest_source(
        source_id=3,
        text=VPD_REFERENCE,
        topic_tags=["vpd", "humidity", "temperature", "environment", "targets"],
        chapter="VPD Reference Guide",
    )


def seed_bugbee_dli(ks: KnowledgeStore) -> int:
    """Seed Bugbee DLI reference data (source_id=1 from initial migration)."""
    return ks.ingest_source(
        source_id=1,
        text=BUGBEE_DLI_REFERENCE,
        topic_tags=["dli", "light", "ppfd", "photosynthesis", "bugbee"],
        chapter="DLI Targets for Cannabis",
    )


def seed_peat_soil(ks: KnowledgeStore) -> int:
    """Seed peat-based soil reference data (source_id=2 from initial migration)."""
    return ks.ingest_source(
        source_id=2,
        text=PEAT_SOIL_REFERENCE,
        topic_tags=["soil", "peat", "watering", "ph", "nutrients", "wp420", "promix"],
        chapter="Peat-Based Soil Growing",
    )


def seed_growhub(ks: KnowledgeStore) -> int:
    """Seed Indo GrowHub 800C reference data (source_id=4 from initial migration)."""
    return ks.ingest_source(
        source_id=4,
        text=GROWHUB_REFERENCE,
        topic_tags=["light", "growhub", "cob", "fan", "ppfd", "distance", "equipment"],
        chapter="Indo GrowHub 800C Reference",
    )


# =============================================================================
# Seed functions for new sources (IDs 5–10, from migration 002)
# =============================================================================

def seed_caplan(ks: KnowledgeStore) -> int:
    """Seed Caplan nutrient/substrate research (source_id=5)."""
    return ks.ingest_source(
        source_id=5,
        text=CAPLAN_REFERENCE,
        topic_tags=["nutrients", "nitrogen", "substrate", "peat", "coir", "ec", "runoff",
                    "managed_fertility", "cannabinoids", "drought_stress"],
        chapter="Caplan et al. — Substrate & Nutrient Research",
    )


def seed_rodriguez_morrison(ks: KnowledgeStore) -> int:
    """Seed Rodriguez-Morrison light science research (source_id=6)."""
    return ks.ingest_source(
        source_id=6,
        text=RODRIGUEZ_MORRISON_REFERENCE,
        topic_tags=["light", "ppfd", "dli", "uv_b", "yield", "potency", "thca",
                    "spectrum", "light_saturation"],
        chapter="Rodriguez-Morrison et al. — Cannabis Light Science",
    )


def seed_westmoreland_bugbee(ks: KnowledgeStore) -> int:
    """Seed Westmoreland/Bugbee spectrum and nutrition research (source_id=7)."""
    return ks.ingest_source(
        source_id=7,
        text=WESTMORELAND_BUGBEE_REFERENCE,
        topic_tags=["spectrum", "blue_light", "red_light", "warm_spectrum", "phosphorus",
                    "p_push", "yield", "nutrients", "cob"],
        chapter="Westmoreland & Bugbee — Spectrum & Nutrition",
    )


def seed_punja(ks: KnowledgeStore) -> int:
    """Seed Punja disease management research (source_id=8)."""
    return ks.ingest_source(
        source_id=8,
        text=PUNJA_REFERENCE,
        topic_tags=["disease", "botrytis", "powdery_mildew", "fusarium", "ipm",
                    "humidity", "bc", "outdoor", "bud_rot", "pathogens"],
        chapter="Punja et al. — Cannabis Pathogens & Disease Management",
    )


def seed_soil_food_web(ks: KnowledgeStore) -> int:
    """Seed Soil Food Web / Ingham / Lowenfels reference (source_id=9)."""
    return ks.ingest_source(
        source_id=9,
        text=SOIL_FOOD_WEB_REFERENCE,
        topic_tags=["living_soil", "biology", "mycorrhizae", "compost", "bacteria",
                    "fungi", "outdoor", "biology_first", "compost_tea", "soil_temp"],
        chapter="Soil Food Web — Ingham, Lowenfels & Lewis",
    )


def seed_bc_ipm(ks: KnowledgeStore) -> int:
    """Seed BC & Regional IPM reference (source_id=10)."""
    return ks.ingest_source(
        source_id=10,
        text=BC_IPM_REFERENCE,
        topic_tags=["ipm", "pest", "bc", "outdoor", "aphids", "spider_mites",
                    "budworm", "botrytis", "slugs", "biocontrol", "vancouver"],
        chapter="BC & Regional IPM — Pacific Northwest Outdoor",
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="Seed Jack's knowledge base")
    parser.add_argument(
        "--source",
        choices=["vpd", "bugbee", "soil", "growhub",
                 "caplan", "rodriguez", "westmoreland", "punja", "soil_web", "bc_ipm",
                 "all"],
        default="all",
        help="Which source to seed (default: all)",
    )
    parser.add_argument("--list", dest="list_sources", action="store_true",
                        help="List all knowledge sources and exit")
    parser.add_argument("--config", default=None, help="Path to jack_config.yaml")
    args = parser.parse_args()

    cfg = load_jack_config(args.config)
    embedder = EmbeddingProvider(cfg)
    ks = KnowledgeStore(cfg.get("database", {}), embedder)

    if args.list_sources:
        sources = ks.list_sources()
        for s in sources:
            status = "✓ ingested" if s["ingested"] else "✗ not ingested"
            print(f"  [{s['id']}] {s['name']} ({s['base_confidence']}) — {status}")
        return

    total = 0

    if args.source in ("vpd", "all"):
        n = seed_vpd_reference(ks)
        logger.info("VPD reference: %d chunks ingested", n)
        total += n

    if args.source in ("bugbee", "all"):
        n = seed_bugbee_dli(ks)
        logger.info("Bugbee DLI: %d chunks ingested", n)
        total += n

    if args.source in ("soil", "all"):
        n = seed_peat_soil(ks)
        logger.info("Peat-based soil: %d chunks ingested", n)
        total += n

    if args.source in ("growhub", "all"):
        n = seed_growhub(ks)
        logger.info("GrowHub 800C: %d chunks ingested", n)
        total += n

    if args.source in ("caplan", "all"):
        n = seed_caplan(ks)
        logger.info("Caplan substrate/nutrients: %d chunks ingested", n)
        total += n

    if args.source in ("rodriguez", "all"):
        n = seed_rodriguez_morrison(ks)
        logger.info("Rodriguez-Morrison light science: %d chunks ingested", n)
        total += n

    if args.source in ("westmoreland", "all"):
        n = seed_westmoreland_bugbee(ks)
        logger.info("Westmoreland/Bugbee spectrum+nutrition: %d chunks ingested", n)
        total += n

    if args.source in ("punja", "all"):
        n = seed_punja(ks)
        logger.info("Punja disease management: %d chunks ingested", n)
        total += n

    if args.source in ("soil_web", "all"):
        n = seed_soil_food_web(ks)
        logger.info("Soil food web: %d chunks ingested", n)
        total += n

    if args.source in ("bc_ipm", "all"):
        n = seed_bc_ipm(ks)
        logger.info("BC & regional IPM: %d chunks ingested", n)
        total += n

    logger.info("Total: %d chunks ingested across all sources", total)


if __name__ == "__main__":
    main()
