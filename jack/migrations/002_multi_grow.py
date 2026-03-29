#!/usr/bin/env python3
"""
jack/migrations/002_multi_grow.py — Multi-grow support (indoor + outdoor).

Schema changes:
  - grows: add grow_type, location, fertility_model columns
  - grows: fix medium default from 'living soil' to 'unspecified'
  - tent_config: add grow_id FK (optional — NULL = shared config)
  - grow_log_entries: add outdoor + managed-fertility specific fields
    (runoff_ec, runoff_ph, brix, soil_temp_c, pest_notes, biology_observations)

Knowledge sources added:
  - 5: Caplan et al. — Cannabis Substrate & Nutrient Research (2017–2019)
  - 6: Rodriguez-Morrison et al. — Cannabis Light Science (2021)
  - 7: Westmoreland, Kusuma & Bugbee — Light Spectrum & Nutrition (2021–2022)
  - 8: Punja et al. — Cannabis Pathogens & Disease Management (2018–2021)
  - 9: Soil Food Web — Ingham, Lowenfels & Lewis
  - 10: BC & Regional IPM — Cannabis Pest Management
"""

SQL = """
-- =============================================================================
-- Migration 002: Multi-Grow Support
-- =============================================================================

-- Add grow classification columns to grows table
ALTER TABLE grows ADD COLUMN IF NOT EXISTS grow_type VARCHAR(20) NOT NULL DEFAULT 'indoor';
ALTER TABLE grows ADD COLUMN IF NOT EXISTS location VARCHAR(100);
ALTER TABLE grows ADD COLUMN IF NOT EXISTS fertility_model VARCHAR(30) NOT NULL DEFAULT 'managed';

-- Fix medium default — 'living soil' was the old default before dual-grow
ALTER TABLE grows ALTER COLUMN medium SET DEFAULT 'unspecified';

COMMENT ON COLUMN grows.grow_type IS 'One of: indoor, outdoor';
COMMENT ON COLUMN grows.location IS 'e.g. "tent" for indoor, "Vancouver backyard" for outdoor';
COMMENT ON COLUMN grows.fertility_model IS 'One of: managed (peat/coco + nutrients), biology_first (living soil)';

-- Associate tent_config with a specific grow (optional — NULL = shared)
ALTER TABLE tent_config ADD COLUMN IF NOT EXISTS grow_id INTEGER REFERENCES grows(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tent_config_grow_id ON tent_config(grow_id);

-- Add managed-fertility and outdoor-specific fields to grow_log_entries
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS runoff_ec DECIMAL(5,2);
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS runoff_ph DECIMAL(3,1);
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS brix DECIMAL(4,1);
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS soil_temp_c DECIMAL(4,1);
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS pest_notes TEXT;
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS biology_observations TEXT;
ALTER TABLE grow_log_entries ADD COLUMN IF NOT EXISTS weather_notes TEXT;

COMMENT ON COLUMN grow_log_entries.runoff_ec IS 'Runoff EC (mS/cm) — managed fertility feedback (Grow A)';
COMMENT ON COLUMN grow_log_entries.runoff_ph IS 'Runoff pH — managed fertility feedback (Grow A)';
COMMENT ON COLUMN grow_log_entries.brix IS 'Brix reading — plant health/sugar indicator (both grows)';
COMMENT ON COLUMN grow_log_entries.soil_temp_c IS 'Soil temperature °C — critical for outdoor biology activation and transplant timing';
COMMENT ON COLUMN grow_log_entries.pest_notes IS 'Pest and disease observations (both grows)';
COMMENT ON COLUMN grow_log_entries.biology_observations IS 'Soil biology indicators: earthworm activity, fungal threads, compost quality (Grow B)';
COMMENT ON COLUMN grow_log_entries.weather_notes IS 'Weather conditions at time of log (outdoor primarily)';

-- =============================================================================
-- Seed: Additional knowledge sources (IDs 5–10)
-- =============================================================================

INSERT INTO knowledge_sources (name, author, source_type, domain_tags, base_confidence, url, notes)
VALUES
    (
        'Caplan et al. — Cannabis Substrate & Nutrient Research (2017–2019)',
        'Caplan, Flaherty, Dixon, Sabeh, Zheng',
        'paper',
        ARRAY['nutrients', 'nitrogen', 'phosphorus', 'substrate', 'peat', 'coir', 'ec', 'ph', 'drought', 'harvest', 'cannabinoids', 'indoor', 'managed_fertility'],
        'very_high',
        'https://doi.org/10.21273/HORTSCI11903-17',
        'Three peer-reviewed studies (Dalhousie University): optimal N rates in veg (2017a), optimal N rates in flower (2017b), drought stress and cannabinoid concentration pre-harvest (2019). Directly applicable to managed-fertility growing in peat/coir substrates. Among the highest-quality applied nutrient research for cannabis.'
    ),
    (
        'Rodriguez-Morrison, Llewellyn & Zheng — Cannabis Light Science (2021)',
        'Rodriguez-Morrison, Llewellyn, Zheng',
        'paper',
        ARRAY['light', 'ppfd', 'dli', 'yield', 'cannabinoids', 'thc', 'uv', 'uv_b', 'spectrum', 'photosaturation', 'indoor'],
        'very_high',
        'https://doi.org/10.3389/fpls.2021.646020',
        'Two Frontiers in Plant Science papers: yield and potency response to PPFD levels (2021a) — yield peaks 600–1000 µmol/m²/s, potency peaks at lower PPFD than yield; UV-B does NOT increase THC/CBD (2021b) — directly contradicts widespread grower belief. High-impact findings.'
    ),
    (
        'Westmoreland, Kusuma & Bugbee — Cannabis Spectrum & Nutrition (2021–2022)',
        'Westmoreland, Kusuma, Bugbee',
        'paper',
        ARRAY['light', 'spectrum', 'blue', 'red', 'far_red', 'yield', 'phosphorus', 'nutrition', 'p_push', 'indoor'],
        'very_high',
        'https://doi.org/10.1371/journal.pone.0248988',
        'Two Utah State (Bugbee lab) studies: decreasing blue photon fraction increases cannabis yield — validates warm-spectrum (3000K) LEDs (2021); elevated root-zone P does not improve yield — common P-push strategy in late flower is not supported by data (2022). Frontiers in Plant Science and PLOS ONE.'
    ),
    (
        'Punja et al. — Cannabis Pathogens & Disease Management (2018–2021)',
        'Punja, Suzuki, Watts, Rodriguez',
        'paper',
        ARRAY['disease', 'pathogen', 'botrytis', 'powdery_mildew', 'fusarium', 'pythium', 'ipm', 'pest', 'bc', 'outdoor', 'indoor', 'grey_mould'],
        'very_high',
        'https://doi.org/10.3389/fpls.2019.01120',
        'Four peer-reviewed studies from Simon Fraser University (Surrey, BC): pathogens and molds affecting cannabis quality (2019), emerging diseases and sustainable management (Pest Mgmt Sci 2021), powdery mildew evaluation and management (Can. J. Plant Pathol. 2021), flower and foliage pathogens (Can. J. Plant Pathol. 2018). Primary BC-relevant disease reference.'
    ),
    (
        'Soil Food Web — Ingham, Lowenfels & Lewis',
        'Dr. Elaine Ingham; Jeff Lowenfels & Wayne Lewis',
        'book',
        ARRAY['living_soil', 'soil_biology', 'bacteria', 'fungi', 'mycorrhizae', 'nematodes', 'protozoa', 'nutrient_cycling', 'compost', 'compost_tea', 'outdoor', 'biology_first', 'containers'],
        'high',
        'https://www.nrcs.usda.gov/resources/education-and-teaching-materials/soil-biology-primer',
        'Foundational soil food web science: Ingham Soil Biology Primer (NRCS/USDA, 1999) and Ingham et al. (1985) bacteria-fungi-nematode nutrient cycling research. Supplemented by Lowenfels & Lewis "Teaming with Microbes" (Timber Press, ISBN 978-1-60469-113-9). Framework for biology-first fertility in outdoor containers (Grow B).'
    ),
    (
        'BC & Regional IPM — Cannabis Pest Management (PNW)',
        'BC Ministry of Agriculture; UC Davis IPM',
        'reference_data',
        ARRAY['ipm', 'pest', 'outdoor', 'bc', 'pnw', 'aphids', 'spider_mites', 'caterpillars', 'budworm', 'slugs', 'botrytis', 'powdery_mildew', 'biocontrol', 'bt', 'beneficial_insects'],
        'high',
        'https://www2.gov.bc.ca/gov/content/industry/agriculture-seafood/agricultural-land-and-environment/integrated-pest-management',
        'BC Ministry of Agriculture IPM guidelines (jurisdiction-specific, legally aligned) and UC Davis IPM Online frameworks. Covers Vancouver/PNW outdoor pest pressure: aphids, spider mites, caterpillars/budworm, slugs, Botrytis in fall humidity. Prevention-first biocontrol approach.'
    )
ON CONFLICT DO NOTHING;
""".strip()

if __name__ == "__main__":
    print(SQL)
