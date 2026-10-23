"""Reviewed, immutable DDL snapshot. Never execute SQL obtained from source files.

Source SCHEMA literals must match these SHA-256 fingerprints exactly (apart from
line endings/outer whitespace). Any drift requires an explicit manifest review.
"""
import ast
import hashlib
from pathlib import Path
from types import MappingProxyType


SOURCE_HASHES = MappingProxyType({
    "storage": "98513f5e02878f598d3009dcde2ba84e344c00322528e01f45b3e272da16e0ea",
    "combat_store": "d375d054bb70218bfc2513993b21b87ec34ae61a01854c0055669359a963d5a9",
    "inventory": "53a7aca9c506ad2cc3153243c202dcf4fd7b561f518f7ce1ef58263d1942df2e",
    "living_dao": "129bea94797881bff447d499d15b232c2b996c9b5cbb90ca1dd6593a8750eddd",
    "sect_halls": "77a49774255246263950d55fde59f696768b4c86940997147f678f9e1b7ad980",
    "sect_v022": "8052822ed961bdcede10007d94d087f4c2b927c9e222a11c23dbef1e798d31f0",
    "bonds_v022": "f23581a3ff2352c609d3f15c0e01baff763423a61de6f7cdc115e7888b4f7863",
})

# Static, reviewed declarations only. No caller-supplied SQL is accepted.
TABLE_DEFINITIONS = (
    ("qi_event_settings", "(guild_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL)"),
    ("qi_event_announcements", "(guild_id BIGINT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(guild_id,event_id))"),
    ("fast_sandboxes", "(guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (guild_id,user_id))"),
    ("fast_sandbox_actions", "(guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,response TEXT NOT NULL,PRIMARY KEY (guild_id,user_id,event_id))"),
    ("characters", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY (guild_id, user_id)\n)"),
    ("trial_sandboxes", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY (guild_id, user_id)\n)"),
    ("sandbox_actions", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n event_id BIGINT NOT NULL, response TEXT NOT NULL,\n PRIMARY KEY (guild_id, user_id, event_id)\n)"),
    ("character_origins", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n root TEXT NOT NULL, background TEXT NOT NULL,\n created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY (guild_id,user_id)\n)"),
    ("processed_actions", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n event_id BIGINT NOT NULL,\n response TEXT NOT NULL,\n PRIMARY KEY (guild_id, user_id, event_id)\n)"),
    ("combat_sessions", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,\n battle JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY (guild_id,user_id))"),
    ("combat_actions", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,\n response TEXT NOT NULL,PRIMARY KEY (guild_id,user_id,event_id))"),
    ("player_inventory", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n holdings JSONB NOT NULL DEFAULT '{}'::jsonb, marks BIGINT NOT NULL DEFAULT 0,\n equipped JSONB NOT NULL DEFAULT '{}'::jsonb,\n updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY (guild_id,user_id))"),
    ("living_dao_players", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,\n data JSONB NOT NULL DEFAULT '{}'::jsonb,\n PRIMARY KEY(guild_id,user_id))"),
    ("living_dao_actions", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,\n response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id))"),
    ("living_dao_sects", "(\n guild_id BIGINT NOT NULL, name TEXT NOT NULL,\n founder_id BIGINT NOT NULL, contribution BIGINT NOT NULL DEFAULT 0,\n PRIMARY KEY(guild_id,name))"),
    ("sect_hall_state", "(\n guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,\n data JSONB NOT NULL DEFAULT '{}'::jsonb,\n PRIMARY KEY (guild_id,sect_name)\n)"),
    ("sect_hall_actions", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event_id BIGINT NOT NULL,\n reply TEXT NOT NULL, PRIMARY KEY(guild_id,user_id,event_id)\n)"),
    ("sect_member_roles", "(\n guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,user_id BIGINT NOT NULL,\n role TEXT NOT NULL DEFAULT 'Disciple', PRIMARY KEY(guild_id,sect_name,user_id)\n)"),
    ("sect_profiles_v022", "(\n guild_id BIGINT NOT NULL,sect_name TEXT NOT NULL,\n banner TEXT NOT NULL DEFAULT 'Unfurl your banner and rise toward the heavens',\n PRIMARY KEY(guild_id,sect_name))"),
    ("sect_raid_v022", "(\n guild_id BIGINT NOT NULL,sect_name TEXT NOT NULL, data JSONB NOT NULL DEFAULT '{}'::jsonb,\n PRIMARY KEY(guild_id,sect_name))"),
    ("sect_action_v022", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,\n response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id))"),
    ("sect_member_join_v022", "(\n guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, sect_name TEXT NOT NULL,\n joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(guild_id,user_id))"),
    ("sect_gathering_history_v022", "(\n guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,\n effective_at TIMESTAMPTZ NOT NULL, level INT NOT NULL,\n PRIMARY KEY(guild_id,sect_name,effective_at))"),
    ("dao_households", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,\n data JSONB NOT NULL DEFAULT '{}'::jsonb,PRIMARY KEY(guild_id,user_id))"),
    ("dao_household_actions", "(\n guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,\n response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id))"),
    ("dao_player_bonds", "(\n guild_id BIGINT NOT NULL, proposer BIGINT NOT NULL, recipient BIGINT NOT NULL,\n role TEXT NOT NULL, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n PRIMARY KEY(guild_id,proposer,recipient),CHECK(proposer<>recipient))"),
    ("dao_dual_requests", "(\n guild_id BIGINT NOT NULL,proposer BIGINT NOT NULL,recipient BIGINT NOT NULL,\n status TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,\n PRIMARY KEY(guild_id,proposer,recipient),CHECK(proposer<>recipient))"),
)


def read_schema_literals():
    result = {}
    for name in SOURCE_HASHES:
        path = Path(__file__).resolve().parent / (name + '.py')
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=name + '.py')
        nodes = [n for n in tree.body if isinstance(n, ast.Assign) and
                 any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets)]
        if len(nodes) != 1 or not isinstance(nodes[0].value, ast.Constant) or not isinstance(nodes[0].value.value, str):
            raise ValueError('Schema source is not a single literal.')
        result[name] = nodes[0].value.value
    return result


def reviewed_table_definitions():
    scripts = read_schema_literals()
    if set(scripts) != set(SOURCE_HASHES):
        raise ValueError('Schema source set changed.')
    for name, script in scripts.items():
        normalized = script.replace('\r\n', '\n').strip()
        if hashlib.sha256(normalized.encode()).hexdigest() != SOURCE_HASHES[name]:
            raise ValueError('Schema source changed; explicit review required.')
    return dict(TABLE_DEFINITIONS)

