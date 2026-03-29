"""
ambient/migrations/002_voice_schema.py

Creates kiro_voices and kiro_emotion_rules tables for the Orpheus TTS engine.
Per ORPHEUS_TTS_INTEGRATION_SPEC.md §3.

Persona voice assignments:
  kiro   → leah (interim; clone_ref_path overrides when Dublin Irish clip is ready)
  finley → dan  (deep male; clone target = Bruce Campbell Burn Notice narration)
  coach  → leo  (energetic male)
  chef   → jess (approachable female)
  doc    → leah (measured, reassuring)
  sage   → mia  (composed, knowledgeable)
  jack   → zac  (enthusiastic)
"""

SQL = """
-- ── Voice config ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kiro_voices (
    id               SERIAL PRIMARY KEY,
    persona          VARCHAR(32) UNIQUE NOT NULL,
    orpheus_voice    VARCHAR(32) NOT NULL DEFAULT 'leah',
    clone_ref_path   TEXT,
    clone_ref_text   TEXT,
    default_emotion  VARCHAR(32) DEFAULT 'neutral',
    speed            FLOAT DEFAULT 1.0,
    temperature      FLOAT DEFAULT 0.6,
    enabled          BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ── Emotion keyword rules ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kiro_emotion_rules (
    id          SERIAL PRIMARY KEY,
    keyword     VARCHAR(128) NOT NULL,
    emotion_tag VARCHAR(32)  NOT NULL,
    priority    INTEGER DEFAULT 0,
    enabled     BOOLEAN DEFAULT TRUE
);

-- ── Seed personas ─────────────────────────────────────────────────────────
INSERT INTO kiro_voices (persona, orpheus_voice, default_emotion, speed, temperature) VALUES
    ('kiro',   'leah', 'warm',         1.0,  0.6),
    ('finley', 'dan',  'confident',    1.0,  0.6),
    ('coach',  'leo',  'encouraging',  1.0,  0.7),
    ('chef',   'jess', 'warm',         1.0,  0.6),
    ('doc',    'leah', 'calm',         0.95, 0.5),
    ('sage',   'mia',  'thoughtful',   0.95, 0.5),
    ('jack',   'zac',  'enthusiastic', 1.0,  0.7),
    ('ruth',   'tara', 'warm',         0.95, 0.5),
    ('lisa',   'jess', 'warm',         1.05, 0.7),
    ('ops',    'leo',  'neutral',      1.0,  0.4)
ON CONFLICT (persona) DO NOTHING;

-- ── Seed emotion rules ────────────────────────────────────────────────────
INSERT INTO kiro_emotion_rules (keyword, emotion_tag, priority) VALUES
    ('congratulations', 'excited',   10),
    ('well done',       'excited',   10),
    ('amazing',         'excited',    8),
    ('great news',      'excited',    9),
    ('unfortunately',   'sigh',       5),
    ('bad news',        'sigh',       8),
    ('sorry to say',    'sigh',       7),
    ('good morning',    'warm',       3),
    ('good night',      'whisper',    3),
    ('check this out',  'excited',    5),
    ('just between us', 'whisper',    6),
    ('wow',             'excited',    4),
    ('oh no',           'gasp',       7),
    ('unexpected',      'gasp',       5)
ON CONFLICT DO NOTHING;
"""

DOWN = """
DROP TABLE IF EXISTS kiro_emotion_rules;
DROP TABLE IF EXISTS kiro_voices;
"""
