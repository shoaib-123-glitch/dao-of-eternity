"""Fixed catalog-only queries. No connection code and no caller-supplied SQL.

Policy is intentionally restrictive. Unknown/NULL results or unsupported catalog
layouts must fail closed. These queries still require approved PostgreSQL validation.
"""

# host(inet) returns address-only text. An explicit inet::text cast always keeps
# the netmask (including /32), which cannot equal the pinned IPv4 host string.
# Python requires the exact reviewed endpoint (127.0.0.1 by default).
IDENTITY_SQL = '''
SELECT pg_catalog.current_database() AS database_name,
 current_user::text AS role_name, session_user::text AS session_role,
 pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address,
 pg_catalog.inet_server_port() AS server_port,
 pg_catalog.current_setting('transaction_read_only') AS read_only,
 pg_catalog.current_setting('default_transaction_read_only') AS default_read_only,
 pg_catalog.current_setting('server_version_num')::int AS version,
 pg_catalog.pg_my_temp_schema() AS temp_schema,
 pg_catalog.pg_is_in_recovery() AS in_recovery,
 r.oid AS role_oid, r.rolcanlogin AS can_login,
 (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls) AS elevated,
 EXISTS (SELECT 1 FROM pg_catalog.pg_roles o WHERE o.oid <> r.oid
         AND pg_catalog.pg_has_role(current_user, o.oid, 'MEMBER')) AS other_memberships,
 pg_catalog.has_database_privilege(current_user, pg_catalog.current_database(), 'CREATE') AS can_create_schema,
 EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtenabled <> 'D') AS enabled_event_triggers
FROM pg_catalog.pg_roles r WHERE r.rolname = current_user
'''

# Only the explicit forwarded-server policy reads this fixed cluster metadata.
# No caller-supplied SQL, grants, settings changes or general file-reading API.
SERVER_IDENTITY_SQL = '''
SELECT system_identifier FROM pg_catalog.pg_control_system()
'''

# Effective privilege functions include PUBLIC, ownership and inherited grants.
# A NULL $1 means no run exists; otherwise it is the separately verified run OID.
# Comma-separated privilege lists mean ANY listed right, not all rights. In
# particular, denied CONNECT does not rule out residual PUBLIC TEMPORARY.
PRIVILEGES_SQL = '''
WITH me AS (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
outside AS (SELECT oid, nspowner FROM pg_catalog.pg_namespace
 WHERE oid <> COALESCE($1::oid, 0::oid)
 AND nspname <> 'information_schema' AND nspname !~ '^pg_')
SELECT
 EXISTS (SELECT 1 FROM pg_catalog.pg_database d, me
  WHERE d.datname <> pg_catalog.current_database() AND d.datallowconn
  AND (d.datdba=me.oid OR pg_catalog.has_database_privilege(current_user,d.oid,'CONNECT,CREATE,TEMPORARY')))
  AS other_database_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
  WHERE n.oid<>COALESCE($1::oid,0::oid)
   AND pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE'))
  AS outside_schema_create,
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n, me
  WHERE n.oid<>COALESCE($1::oid,0::oid) AND n.nspowner=me.oid) AS outside_schema_ownership,
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c, me
  WHERE c.relnamespace<>COALESCE($1::oid,0::oid)
   AND (c.relnamespace IN (SELECT oid FROM outside) OR c.oid>=16384)
   AND c.relkind IN ('r','p','v','m','f') AND (c.relowner=me.oid
   OR pg_catalog.has_table_privilege(current_user,c.oid,
    CASE WHEN pg_catalog.current_setting('server_version_num')::int>=170000
     THEN 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
     ELSE 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER' END)
   OR pg_catalog.has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
  AS outside_table_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c, me
  WHERE c.relnamespace<>COALESCE($1::oid,0::oid)
   AND c.relnamespace NOT IN (SELECT oid FROM outside) AND c.oid<16384
   AND c.relkind IN ('r','p','v','m','f')
   AND (c.relowner=me.oid OR pg_catalog.has_table_privilege(current_user,c.oid,
    CASE WHEN pg_catalog.current_setting('server_version_num')::int>=170000
     THEN 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
     ELSE 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER' END)
    OR pg_catalog.has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
  AS system_catalog_writes,
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c, me
  WHERE c.relnamespace<>COALESCE($1::oid,0::oid) AND c.relkind='S' AND (c.relowner=me.oid
   OR pg_catalog.has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')))
  AS outside_sequence_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace, me
  WHERE p.proowner=me.oid
   OR (pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AND
     (n.oid IN (SELECT oid FROM outside) OR p.prosecdef OR p.oid>=16384 OR
      (n.nspname='pg_catalog' AND
       (p.proname IN ('lo_import','lo_export','pg_stat_file')
        OR p.proname LIKE 'pg_read_%file%' OR p.proname LIKE 'pg_ls_%dir%')))))
  AS outside_function_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl a, me,
  LATERAL pg_catalog.aclexplode(a.defaclacl) x
  WHERE a.defaclrole=me.oid AND a.defaclnamespace IN (0::oid,COALESCE($1::oid,0::oid))
    AND x.grantee<>me.oid) AS unsafe_defaults,
 (EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server s
           WHERE pg_catalog.has_server_privilege(current_user,s.oid,'USAGE'))
  OR EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper f
           WHERE pg_catalog.has_foreign_data_wrapper_privilege(current_user,f.oid,'USAGE'))
  OR EXISTS (SELECT 1 FROM pg_catalog.pg_user_mappings m, me WHERE m.umuser IN (0::oid,me.oid)))
  AS foreign_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_largeobject_metadata l, me
  WHERE l.lomowner=me.oid OR EXISTS (
   SELECT 1 FROM pg_catalog.aclexplode(COALESCE(l.lomacl,pg_catalog.acldefault('L',l.lomowner))) x
   WHERE CASE WHEN x.grantee=0 THEN true
    ELSE pg_catalog.pg_has_role(current_user,x.grantee,'USAGE') END))
  AS large_object_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_parameter_acl a,
  LATERAL pg_catalog.aclexplode(a.paracl) x
  WHERE CASE WHEN x.grantee=0 THEN true
   ELSE pg_catalog.pg_has_role(current_user,x.grantee,'USAGE') END) AS unsafe_parameter_access,
 EXISTS (SELECT 1 FROM pg_catalog.pg_tablespace s
  WHERE pg_catalog.has_tablespace_privilege(current_user,s.oid,'CREATE')) AS tablespace_access
'''
PRIVILEGE_FLAGS = (
    'other_database_access', 'outside_schema_create', 'outside_schema_ownership',
    'outside_table_access', 'system_catalog_writes', 'outside_sequence_access', 'outside_function_access',
    'unsafe_defaults', 'foreign_access', 'large_object_access', 'unsafe_parameter_access',
    'tablespace_access',
)

# Diagnostics reuse these already-read Booleans; they never issue extra SQL.
ROLE_PRIVILEGE_FLAGS = ('can_login', 'elevated', 'other_memberships', 'enabled_event_triggers')
CATALOG_WRITE_METADATA_FLAGS = (
    'is_pg_settings', 'owned', 'other_table_access', 'other_column_access',
    'table_update', 'update_acl_safe', 'explicit_column_update',
)
CATALOG_WRITE_DIAGNOSTIC_FLAGS = (
    'single_catalog_write_candidate', 'standard_pg_settings_update',
    'builtin_catalog_oid', 'pg_catalog_schema', 'pg_settings_name', 'view_kind',
    *CATALOG_WRITE_METADATA_FLAGS,
)

# Inspect the exact effective modifications when the broad catalog flag is true.
# Python permits only the standard PUBLIC UPDATE on the built-in pg_settings
# view. All other grants, relations and parameter-level privileges still reject.
CATALOG_WRITES_SQL = '''
WITH me AS (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
candidates AS (
 SELECT c.*, n.nspname FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relnamespace<>COALESCE($1::oid,0::oid) AND c.oid<16384
  AND (n.nspname='information_schema' OR n.nspname ~ '^pg_')
  AND c.relkind IN ('r','p','v','m','f')
  AND (c.relowner=(SELECT oid FROM me) OR pg_catalog.has_table_privilege(current_user,c.oid,
   CASE WHEN pg_catalog.current_setting('server_version_num')::int>=170000
    THEN 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
    ELSE 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER' END)
   OR pg_catalog.has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
SELECT c.oid, c.nspname::text AS schema, c.relname::text AS name, c.relkind::text AS kind,
 c.oid='pg_catalog.pg_settings'::regclass::oid AS is_pg_settings,
 c.relowner=me.oid AS owned,
 pg_catalog.has_table_privilege(current_user,c.oid,
  CASE WHEN pg_catalog.current_setting('server_version_num')::int>=170000
   THEN 'INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
   ELSE 'INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER' END)
  AS other_table_access,
 pg_catalog.has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES') AS other_column_access,
 pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE') AS table_update,
 (EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) x
   WHERE x.privilege_type='UPDATE' AND x.grantee=0 AND x.grantor=c.relowner AND NOT x.is_grantable)
  AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) x
   WHERE x.privilege_type='UPDATE'
    AND CASE WHEN x.grantee=0 THEN true ELSE pg_catalog.pg_has_role(current_user,x.grantee,'USAGE') END
    AND (x.grantee<>0 OR x.grantor<>c.relowner OR x.is_grantable))) AS update_acl_safe,
 EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a,
  LATERAL pg_catalog.aclexplode(a.attacl) x
  WHERE a.attrelid=c.oid AND x.privilege_type='UPDATE'
   AND CASE WHEN x.grantee=0 THEN true ELSE pg_catalog.pg_has_role(current_user,x.grantee,'USAGE') END)
  AS explicit_column_update
FROM candidates c, me
'''

NAMESPACE_SQL = '''
SELECT n.oid, n.nspowner AS owner,
 NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(
  COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) x
  WHERE x.grantee<>n.nspowner) AS acl_safe
FROM pg_catalog.pg_namespace n WHERE n.nspname=$1
'''

# Adoption is only for an administrator-provisioned, genuinely empty namespace.
# Namespace dependencies cover tables, types, routines and other named objects;
# extension namespace placement is also checked explicitly.
EMPTY_NAMESPACE_SQL = '''
SELECT NOT EXISTS (
 SELECT 1 FROM pg_catalog.pg_depend
 WHERE refclassid='pg_catalog.pg_namespace'::regclass AND refobjid=$1::oid
) AND NOT EXISTS (
 SELECT 1 FROM pg_catalog.pg_extension WHERE extnamespace=$1::oid
) AS empty_namespace
'''
PATH_SQL = '''
SELECT pg_catalog.current_schema() AS current_schema,
 pg_catalog.current_schemas(false) AS schemas,
 pg_catalog.pg_my_temp_schema() AS temp_schema
'''

# Include TOAST relations/indexes and the objects DROP TABLE removes implicitly.
# Object addresses and definitions are snapshotted, not merely table names.
# Any incoming dependency from an object outside this inventory is rejected,
# including automatic dependencies that RESTRICT alone would not protect.
# NULL composite/array type ACLs retain PostgreSQL's standard PUBLIC USAGE.
# These approved row types expose no rows; the schema/table ACLs remain private.
# Explicit type grants to anyone else reject, as do non-manifest initial types.
INVENTORY_SQL = '''
WITH base AS (SELECT oid,reltoastrelid FROM pg_catalog.pg_class WHERE relnamespace=$1::oid),
relation_ids AS (
 SELECT oid FROM base UNION SELECT reltoastrelid FROM base WHERE reltoastrelid<>0
 UNION SELECT i.indexrelid FROM pg_catalog.pg_index i
 WHERE i.indrelid IN (SELECT reltoastrelid FROM base WHERE reltoastrelid<>0)),
rels AS (SELECT c.* FROM pg_catalog.pg_class c JOIN relation_ids r ON r.oid=c.oid),
objects AS (
 SELECT 'namespace'::text AS kind, 'pg_catalog.pg_namespace'::regclass::oid AS classid,
  n.oid AS oid, 0::int AS subid, n.nspname::text AS name, 0::oid AS parent,
  n.nspowner AS owner, n.nspacl::text AS definition,
  NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(n.nspacl,
   pg_catalog.acldefault('n',n.nspowner))) x WHERE x.grantee<>n.nspowner) AS acl_safe
 FROM pg_catalog.pg_namespace n WHERE n.oid=$1::oid
 UNION ALL
 SELECT 'relation', 'pg_catalog.pg_class'::regclass::oid, c.oid, 0, c.relname::text,
  COALESCE(i.indrelid,0::oid), c.relowner,
  pg_catalog.jsonb_build_object('kind',c.relkind,'namespace',c.relnamespace,
   'persistence',c.relpersistence,'toast',c.reltoastrelid,'rowtype',c.reltype,
   'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'acl',c.relacl::text,
   'primary_index',i.indisprimary,'index',CASE WHEN c.relkind='i' THEN pg_catalog.pg_get_indexdef(c.oid) END)::text,
  NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
   pg_catalog.acldefault('r',c.relowner))) x WHERE x.grantee<>c.relowner)
 FROM rels c LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
 UNION ALL
 SELECT 'column','pg_catalog.pg_class'::regclass::oid,c.oid,a.attnum,a.attname::text,c.oid,c.relowner,
  pg_catalog.jsonb_build_object('type',a.atttypid,'mod',a.atttypmod,'notnull',a.attnotnull,
   'identity',a.attidentity,'generated',a.attgenerated,'dropped',a.attisdropped,
   'acl',a.attacl::text,'collation',a.attcollation)::text,
  NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(a.attacl,'{}'::aclitem[])) x
   WHERE x.grantee<>c.relowner)
 FROM rels c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid WHERE a.attnum>0
 UNION ALL
 SELECT 'constraint','pg_catalog.pg_constraint'::regclass::oid,k.oid,0,k.conname::text,
  k.conrelid,c.relowner,
  pg_catalog.jsonb_build_object('type',k.contype,'columns',k.conkey,
   'definition',pg_catalog.pg_get_constraintdef(k.oid,true),'validated',k.convalidated,
   'enforced',COALESCE((pg_catalog.to_jsonb(k)->>'conenforced')::bool,true),
   'deferrable',k.condeferrable,'deferred',k.condeferred,'local',k.conislocal,
   'inherited',k.coninhcount,'no_inherit',k.connoinherit)::text,true
 FROM pg_catalog.pg_constraint k JOIN rels c ON c.oid=k.conrelid
 UNION ALL
 SELECT 'default','pg_catalog.pg_attrdef'::regclass::oid,d.oid,0,d.adnum::text,d.adrelid,c.relowner,
  pg_catalog.pg_get_expr(d.adbin,d.adrelid),true
 FROM pg_catalog.pg_attrdef d JOIN rels c ON c.oid=d.adrelid
 UNION ALL
 SELECT 'trigger','pg_catalog.pg_trigger'::regclass::oid,t.oid,0,t.tgname::text,t.tgrelid,c.relowner,
  pg_catalog.pg_get_triggerdef(t.oid,true),true
 FROM pg_catalog.pg_trigger t JOIN rels c ON c.oid=t.tgrelid
 UNION ALL
 SELECT 'rule','pg_catalog.pg_rewrite'::regclass::oid,r.oid,0,r.rulename::text,r.ev_class,c.relowner,
  pg_catalog.pg_get_ruledef(r.oid,true),true
 FROM pg_catalog.pg_rewrite r JOIN rels c ON c.oid=r.ev_class
 UNION ALL
 SELECT 'policy','pg_catalog.pg_policy'::regclass::oid,p.oid,0,p.polname::text,p.polrelid,c.relowner,
  pg_catalog.jsonb_build_object('command',p.polcmd,'roles',p.polroles,'permissive',p.polpermissive,
   'using',pg_catalog.pg_get_expr(p.polqual,p.polrelid),
   'check',pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid))::text,true
 FROM pg_catalog.pg_policy p JOIN rels c ON c.oid=p.polrelid
 UNION ALL
 SELECT 'statistics','pg_catalog.pg_statistic_ext'::regclass::oid,s.oid,0,s.stxname::text,
  s.stxrelid,s.stxowner,s.stxkeys::text||s.stxkind::text,true
 FROM pg_catalog.pg_statistic_ext s JOIN rels c ON c.oid=s.stxrelid
 UNION ALL
 SELECT 'type','pg_catalog.pg_type'::regclass::oid,t.oid,0,t.typname::text,t.typrelid,t.typowner,
  pg_catalog.jsonb_build_object('kind',t.typtype,'element',t.typelem,'relation',t.typrelid,
   'acl',t.typacl::text,'input',t.typinput,'output',t.typoutput)::text,
  NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(t.typacl,'{}'::aclitem[])) x
   WHERE x.grantee<>t.typowner)
 FROM pg_catalog.pg_type t WHERE t.typnamespace=$1::oid
), unsafe AS (
 SELECT 1 FROM pg_catalog.pg_depend d
 JOIN objects root ON root.classid=d.refclassid AND root.oid=d.refobjid
 WHERE NOT EXISTS (SELECT 1 FROM objects child WHERE child.classid=d.classid AND child.oid=d.objid)
)
SELECT objects.*, NOT EXISTS(SELECT 1 FROM unsafe) AS dependencies_safe FROM objects
'''

# Fixed recovery observation only. No object definitions, query text or rows of
# player data are returned. pg_depend covers types/routines as well as relations.
RECOVERY_OBSERVATION_SQL = '''
SELECT
 EXISTS (SELECT 1 FROM pg_catalog.pg_database
  WHERE datname=pg_catalog.current_database() AND oid=$4::oid) AS database_oid_verified,
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=$1::text) AS namespace_exists,
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
  WHERE nspname=$1::text AND oid=$2::oid AND nspowner=$3::oid) AS namespace_identity_verified,
 EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
  WHERE n.nspname=$1::text AND n.oid=$2::oid
  AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(
   COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) x
   WHERE x.grantee<>n.nspowner)) AS acl_private,
 pg_catalog.has_schema_privilege(current_user,$2::oid,'USAGE') AS schema_usage,
 pg_catalog.has_schema_privilege(current_user,$2::oid,'CREATE') AS schema_create,
 NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relnamespace=$2::oid) AS no_relations,
 NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend
  WHERE refclassid='pg_catalog.pg_namespace'::regclass AND refobjid=$2::oid) AS no_namespace_dependencies,
 NOT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extnamespace=$2::oid) AS no_extension_placement,
 NOT EXISTS (SELECT 1 FROM pg_catalog.pg_stat_activity
  WHERE datname=pg_catalog.current_database() AND usename=current_user
   AND application_name='dao_integration_guard' AND pid<>pg_catalog.pg_backend_pid()) AS no_other_guard_sessions
'''
