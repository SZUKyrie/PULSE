#!/usr/bin/env bash
#
# setup_tpch.sh — Generate TPC-H data (SF1) and load into PostgreSQL.
#
# Prerequisites:
#   - PostgreSQL 16+ running locally
#   - dbgen compiled (https://github.com/gregrahn/tpch-kit)
#
# Usage:
#   ./scripts/setup_tpch.sh [SCALE_FACTOR] [DBGEN_DIR]
#
# Defaults:
#   SCALE_FACTOR = 1
#   DBGEN_DIR = ./tpch-kit/dbgen

set -euo pipefail

SCALE_FACTOR="${1:-1}"
DBGEN_DIR="${2:-./tpch-kit/dbgen}"
DB_NAME="tpch"
DB_USER="${PGUSER:-$(whoami)}"
DB_HOST="${PGHOST:-localhost}"
DB_PORT="${PGPORT:-5432}"

echo "=== TPC-H Setup Script ==="
echo "Scale Factor: ${SCALE_FACTOR}"
echo "dbgen Dir:    ${DBGEN_DIR}"
echo "Database:     ${DB_NAME}"
echo "Host:         ${DB_HOST}:${DB_PORT}"
echo ""

# ─── Step 1: Generate data ─────────────────────────────────────────────────────

if [ ! -f "${DBGEN_DIR}/dbgen" ]; then
    echo "ERROR: dbgen not found at ${DBGEN_DIR}/dbgen"
    echo "Build it first:"
    echo "  git clone https://github.com/gregrahn/tpch-kit.git"
    echo "  cd tpch-kit/dbgen && make MACHINE=MACOS DATABASE=POSTGRESQL"
    exit 1
fi

echo ">>> Generating TPC-H data (SF${SCALE_FACTOR})..."
cd "${DBGEN_DIR}"
./dbgen -f -s "${SCALE_FACTOR}"
echo "    Data generated."
cd - > /dev/null

# ─── Step 2: Create database ───────────────────────────────────────────────────

echo ">>> Creating database '${DB_NAME}'..."
dropdb --if-exists -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "${DB_NAME}" 2>/dev/null || true
createdb -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "${DB_NAME}"
echo "    Database created."

# ─── Step 3: Create tables ─────────────────────────────────────────────────────

echo ">>> Creating TPC-H tables..."
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -q <<'SQL'

-- Nation and Region
CREATE TABLE region (
    r_regionkey  INTEGER NOT NULL,
    r_name       CHAR(25) NOT NULL,
    r_comment    VARCHAR(152),
    PRIMARY KEY (r_regionkey)
);

CREATE TABLE nation (
    n_nationkey  INTEGER NOT NULL,
    n_name       CHAR(25) NOT NULL,
    n_regionkey  INTEGER NOT NULL,
    n_comment    VARCHAR(152),
    PRIMARY KEY (n_nationkey),
    FOREIGN KEY (n_regionkey) REFERENCES region(r_regionkey)
);

-- Supplier
CREATE TABLE supplier (
    s_suppkey    INTEGER NOT NULL,
    s_name       CHAR(25) NOT NULL,
    s_address    VARCHAR(40) NOT NULL,
    s_nationkey  INTEGER NOT NULL,
    s_phone      CHAR(15) NOT NULL,
    s_acctbal    DECIMAL(15,2) NOT NULL,
    s_comment    VARCHAR(101) NOT NULL,
    PRIMARY KEY (s_suppkey),
    FOREIGN KEY (s_nationkey) REFERENCES nation(n_nationkey)
);

-- Part
CREATE TABLE part (
    p_partkey    INTEGER NOT NULL,
    p_name       VARCHAR(55) NOT NULL,
    p_mfgr       CHAR(25) NOT NULL,
    p_brand      CHAR(10) NOT NULL,
    p_type       VARCHAR(25) NOT NULL,
    p_size       INTEGER NOT NULL,
    p_container  CHAR(10) NOT NULL,
    p_retailprice DECIMAL(15,2) NOT NULL,
    p_comment    VARCHAR(23) NOT NULL,
    PRIMARY KEY (p_partkey)
);

-- PartSupp
CREATE TABLE partsupp (
    ps_partkey    INTEGER NOT NULL,
    ps_suppkey    INTEGER NOT NULL,
    ps_availqty   INTEGER NOT NULL,
    ps_supplycost DECIMAL(15,2) NOT NULL,
    ps_comment    VARCHAR(199) NOT NULL,
    PRIMARY KEY (ps_partkey, ps_suppkey),
    FOREIGN KEY (ps_partkey) REFERENCES part(p_partkey),
    FOREIGN KEY (ps_suppkey) REFERENCES supplier(s_suppkey)
);

-- Customer
CREATE TABLE customer (
    c_custkey    INTEGER NOT NULL,
    c_name       VARCHAR(25) NOT NULL,
    c_address    VARCHAR(40) NOT NULL,
    c_nationkey  INTEGER NOT NULL,
    c_phone      CHAR(15) NOT NULL,
    c_acctbal    DECIMAL(15,2) NOT NULL,
    c_mktsegment CHAR(10) NOT NULL,
    c_comment    VARCHAR(117) NOT NULL,
    PRIMARY KEY (c_custkey),
    FOREIGN KEY (c_nationkey) REFERENCES nation(n_nationkey)
);

-- Orders
CREATE TABLE orders (
    o_orderkey     INTEGER NOT NULL,
    o_custkey      INTEGER NOT NULL,
    o_orderstatus  CHAR(1) NOT NULL,
    o_totalprice   DECIMAL(15,2) NOT NULL,
    o_orderdate    DATE NOT NULL,
    o_orderpriority CHAR(15) NOT NULL,
    o_clerk        CHAR(15) NOT NULL,
    o_shippriority INTEGER NOT NULL,
    o_comment      VARCHAR(79) NOT NULL,
    PRIMARY KEY (o_orderkey),
    FOREIGN KEY (o_custkey) REFERENCES customer(c_custkey)
);

-- Lineitem
CREATE TABLE lineitem (
    l_orderkey      INTEGER NOT NULL,
    l_partkey       INTEGER NOT NULL,
    l_suppkey       INTEGER NOT NULL,
    l_linenumber    INTEGER NOT NULL,
    l_quantity      DECIMAL(15,2) NOT NULL,
    l_extendedprice DECIMAL(15,2) NOT NULL,
    l_discount      DECIMAL(15,2) NOT NULL,
    l_tax           DECIMAL(15,2) NOT NULL,
    l_returnflag    CHAR(1) NOT NULL,
    l_linestatus    CHAR(1) NOT NULL,
    l_shipdate      DATE NOT NULL,
    l_commitdate    DATE NOT NULL,
    l_receiptdate   DATE NOT NULL,
    l_shipinstruct  CHAR(25) NOT NULL,
    l_shipmode      CHAR(10) NOT NULL,
    l_comment       VARCHAR(44) NOT NULL,
    PRIMARY KEY (l_orderkey, l_linenumber),
    FOREIGN KEY (l_orderkey) REFERENCES orders(o_orderkey),
    FOREIGN KEY (l_partkey, l_suppkey) REFERENCES partsupp(ps_partkey, ps_suppkey)
);

SQL
echo "    Tables created."

# ─── Step 4: Load data ─────────────────────────────────────────────────────────

echo ">>> Loading TPC-H data..."
DATA_DIR="${DBGEN_DIR}"

# TPC-H dbgen produces files with trailing '|' — use a custom delimiter approach
for table in region nation supplier part partsupp customer orders lineitem; do
    file="${DATA_DIR}/${table}.tbl"
    if [ ! -f "${file}" ]; then
        echo "    WARNING: ${file} not found, skipping."
        continue
    fi
    echo "    Loading ${table}..."
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -q \
        -c "\\COPY ${table} FROM '${file}' WITH (FORMAT csv, DELIMITER '|', NULL '')"
done
echo "    Data loaded."

# ─── Step 5: Create indexes ───────────────────────────────────────────────────

echo ">>> Creating indexes..."
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -q <<'SQL'

-- Lineitem indexes (most critical for TPC-H performance)
CREATE INDEX idx_lineitem_shipdate ON lineitem (l_shipdate);
CREATE INDEX idx_lineitem_orderkey ON lineitem (l_orderkey);
CREATE INDEX idx_lineitem_partkey ON lineitem (l_partkey);
CREATE INDEX idx_lineitem_suppkey ON lineitem (l_suppkey);
CREATE INDEX idx_lineitem_partsupp ON lineitem (l_partkey, l_suppkey);
CREATE INDEX idx_lineitem_commitdate ON lineitem (l_commitdate);
CREATE INDEX idx_lineitem_receiptdate ON lineitem (l_receiptdate);

-- Orders indexes
CREATE INDEX idx_orders_custkey ON orders (o_custkey);
CREATE INDEX idx_orders_orderdate ON orders (o_orderdate);

-- Customer indexes
CREATE INDEX idx_customer_nationkey ON customer (c_nationkey);
CREATE INDEX idx_customer_mktsegment ON customer (c_mktsegment);

-- Supplier indexes
CREATE INDEX idx_supplier_nationkey ON supplier (s_nationkey);

-- PartSupp indexes
CREATE INDEX idx_partsupp_suppkey ON partsupp (ps_suppkey);
CREATE INDEX idx_partsupp_partkey ON partsupp (ps_partkey);

-- Nation/Region indexes
CREATE INDEX idx_nation_regionkey ON nation (n_regionkey);

-- Part indexes
CREATE INDEX idx_part_type ON part (p_type);
CREATE INDEX idx_part_brand ON part (p_brand);
CREATE INDEX idx_part_size ON part (p_size);

SQL
echo "    Indexes created."

# ─── Step 6: Analyze ──────────────────────────────────────────────────────────

echo ">>> Running ANALYZE on all tables..."
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -q \
    -c "ANALYZE;"
echo "    ANALYZE complete."

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "=== TPC-H setup complete ==="
echo "Database '${DB_NAME}' is ready with SF${SCALE_FACTOR} data."
echo ""
echo "Connect with: psql -d ${DB_NAME}"
echo "Verify with:  psql -d ${DB_NAME} -c 'SELECT COUNT(*) FROM lineitem;'"
echo "  Expected ~6,001,215 rows for SF1."
