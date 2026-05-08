"""
TpchLoader — Loads the 22 TPC-H benchmark queries with natural language descriptions.

TPC-H is a decision-support benchmark consisting of 22 business-oriented
ad-hoc queries. Each query has a natural language description, a gold SQL
template, and a scale factor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TpchQuery:
    """A single TPC-H benchmark query."""

    query_id: int
    question: str  # Natural language description
    gold_sql: str
    scale_factor: float  # SF used for this benchmark run


# The 22 TPC-H queries with natural language descriptions and gold SQL
TPCH_QUERIES: list[dict] = [
    {
        "query_id": 1,
        "question": "Report the amount of business that was billed, shipped, and returned. Summarize pricing and discount information for all lineitems shipped before a given date.",
        "gold_sql": """
SELECT
    l_returnflag,
    l_linestatus,
    SUM(l_quantity) AS sum_qty,
    SUM(l_extendedprice) AS sum_base_price,
    SUM(l_extendedprice * (1 - l_discount)) AS sum_disc_price,
    SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS sum_charge,
    AVG(l_quantity) AS avg_qty,
    AVG(l_extendedprice) AS avg_price,
    AVG(l_discount) AS avg_disc,
    COUNT(*) AS count_order
FROM lineitem
WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL '90 days'
GROUP BY l_returnflag, l_linestatus
ORDER BY l_returnflag, l_linestatus;
""",
    },
    {
        "query_id": 2,
        "question": "Find the minimum cost supplier in a given region for parts of a given type and size.",
        "gold_sql": """
SELECT
    s_acctbal, s_name, n_name, p_partkey, p_mfgr,
    s_address, s_phone, s_comment
FROM part, supplier, partsupp, nation, region
WHERE p_partkey = ps_partkey
  AND s_suppkey = ps_suppkey
  AND p_size = 15
  AND p_type LIKE '%BRASS'
  AND s_nationkey = n_nationkey
  AND n_regionkey = r_regionkey
  AND r_name = 'EUROPE'
  AND ps_supplycost = (
      SELECT MIN(ps_supplycost)
      FROM partsupp, supplier, nation, region
      WHERE p_partkey = ps_partkey
        AND s_suppkey = ps_suppkey
        AND s_nationkey = n_nationkey
        AND n_regionkey = r_regionkey
        AND r_name = 'EUROPE'
  )
ORDER BY s_acctbal DESC, n_name, s_name, p_partkey
LIMIT 100;
""",
    },
    {
        "query_id": 3,
        "question": "Retrieve the 10 unshipped orders with the highest value for a given market segment and order date.",
        "gold_sql": """
SELECT
    l_orderkey,
    SUM(l_extendedprice * (1 - l_discount)) AS revenue,
    o_orderdate,
    o_shippriority
FROM customer, orders, lineitem
WHERE c_mktsegment = 'BUILDING'
  AND c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate < DATE '1995-03-15'
  AND l_shipdate > DATE '1995-03-15'
GROUP BY l_orderkey, o_orderdate, o_shippriority
ORDER BY revenue DESC, o_orderdate
LIMIT 10;
""",
    },
    {
        "query_id": 4,
        "question": "Count the number of orders in each order priority bucket where at least one lineitem was received late, for a given quarter.",
        "gold_sql": """
SELECT
    o_orderpriority,
    COUNT(*) AS order_count
FROM orders
WHERE o_orderdate >= DATE '1993-07-01'
  AND o_orderdate < DATE '1993-07-01' + INTERVAL '3 months'
  AND EXISTS (
      SELECT * FROM lineitem
      WHERE l_orderkey = o_orderkey
        AND l_commitdate < l_receiptdate
  )
GROUP BY o_orderpriority
ORDER BY o_orderpriority;
""",
    },
    {
        "query_id": 5,
        "question": "List the revenue volume from local suppliers in a given region, for a given year.",
        "gold_sql": """
SELECT
    n_name,
    SUM(l_extendedprice * (1 - l_discount)) AS revenue
FROM customer, orders, lineitem, supplier, nation, region
WHERE c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND l_suppkey = s_suppkey
  AND c_nationkey = s_nationkey
  AND s_nationkey = n_nationkey
  AND n_regionkey = r_regionkey
  AND r_name = 'ASIA'
  AND o_orderdate >= DATE '1994-01-01'
  AND o_orderdate < DATE '1994-01-01' + INTERVAL '1 year'
GROUP BY n_name
ORDER BY revenue DESC;
""",
    },
    {
        "query_id": 6,
        "question": "Compute the revenue increase that would have resulted from eliminating certain discounts in a given year. Show the total revenue change for lineitems meeting discount and quantity criteria.",
        "gold_sql": """
SELECT
    SUM(l_extendedprice * l_discount) AS revenue
FROM lineitem
WHERE l_shipdate >= DATE '1994-01-01'
  AND l_shipdate < DATE '1994-01-01' + INTERVAL '1 year'
  AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01
  AND l_quantity < 24;
""",
    },
    {
        "query_id": 7,
        "question": "Determine the value of goods shipped between certain nations in certain years to help in re-negotiating shipping contracts.",
        "gold_sql": """
SELECT
    supp_nation,
    cust_nation,
    l_year,
    SUM(volume) AS revenue
FROM (
    SELECT
        n1.n_name AS supp_nation,
        n2.n_name AS cust_nation,
        EXTRACT(YEAR FROM l_shipdate) AS l_year,
        l_extendedprice * (1 - l_discount) AS volume
    FROM supplier, lineitem, orders, customer, nation n1, nation n2
    WHERE s_suppkey = l_suppkey
      AND o_orderkey = l_orderkey
      AND c_custkey = o_custkey
      AND s_nationkey = n1.n_nationkey
      AND c_nationkey = n2.n_nationkey
      AND (
          (n1.n_name = 'FRANCE' AND n2.n_name = 'GERMANY')
          OR (n1.n_name = 'GERMANY' AND n2.n_name = 'FRANCE')
      )
      AND l_shipdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
) AS shipping
GROUP BY supp_nation, cust_nation, l_year
ORDER BY supp_nation, cust_nation, l_year;
""",
    },
    {
        "query_id": 8,
        "question": "Determine how the market share of a given nation within a given region has changed over two years for a given part type.",
        "gold_sql": """
SELECT
    o_year,
    SUM(CASE WHEN nation = 'BRAZIL' THEN volume ELSE 0 END) / SUM(volume) AS mkt_share
FROM (
    SELECT
        EXTRACT(YEAR FROM o_orderdate) AS o_year,
        l_extendedprice * (1 - l_discount) AS volume,
        n2.n_name AS nation
    FROM part, supplier, lineitem, orders, customer, nation n1, nation n2, region
    WHERE p_partkey = l_partkey
      AND s_suppkey = l_suppkey
      AND l_orderkey = o_orderkey
      AND o_custkey = c_custkey
      AND c_nationkey = n1.n_nationkey
      AND n1.n_regionkey = r_regionkey
      AND r_name = 'AMERICA'
      AND s_nationkey = n2.n_nationkey
      AND o_orderdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
      AND p_type = 'ECONOMY ANODIZED STEEL'
) AS all_nations
GROUP BY o_year
ORDER BY o_year;
""",
    },
    {
        "query_id": 9,
        "question": "Determine how much profit is made on a given line of parts, broken out by supplier nation and year.",
        "gold_sql": """
SELECT
    nation,
    o_year,
    SUM(amount) AS sum_profit
FROM (
    SELECT
        n_name AS nation,
        EXTRACT(YEAR FROM o_orderdate) AS o_year,
        l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity AS amount
    FROM part, supplier, lineitem, partsupp, orders, nation
    WHERE s_suppkey = l_suppkey
      AND ps_suppkey = l_suppkey
      AND ps_partkey = l_partkey
      AND p_partkey = l_partkey
      AND o_orderkey = l_orderkey
      AND s_nationkey = n_nationkey
      AND p_name LIKE '%green%'
) AS profit
GROUP BY nation, o_year
ORDER BY nation, o_year DESC;
""",
    },
    {
        "query_id": 10,
        "question": "Identify customers who might be having problems with the parts that are shipped to them. Find the top 20 customers by lost revenue due to returned items in a given quarter.",
        "gold_sql": """
SELECT
    c_custkey, c_name,
    SUM(l_extendedprice * (1 - l_discount)) AS revenue,
    c_acctbal, n_name, c_address, c_phone, c_comment
FROM customer, orders, lineitem, nation
WHERE c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate >= DATE '1993-10-01'
  AND o_orderdate < DATE '1993-10-01' + INTERVAL '3 months'
  AND l_returnflag = 'R'
  AND c_nationkey = n_nationkey
GROUP BY c_custkey, c_name, c_acctbal, c_phone, n_name, c_address, c_comment
ORDER BY revenue DESC
LIMIT 20;
""",
    },
    {
        "query_id": 11,
        "question": "Find the most important subset of a given supplier's stock in a given nation that represents a significant percentage of the total value.",
        "gold_sql": """
SELECT
    ps_partkey,
    SUM(ps_supplycost * ps_availqty) AS value
FROM partsupp, supplier, nation
WHERE ps_suppkey = s_suppkey
  AND s_nationkey = n_nationkey
  AND n_name = 'GERMANY'
GROUP BY ps_partkey
HAVING SUM(ps_supplycost * ps_availqty) > (
    SELECT SUM(ps_supplycost * ps_availqty) * 0.0001
    FROM partsupp, supplier, nation
    WHERE ps_suppkey = s_suppkey
      AND s_nationkey = n_nationkey
      AND n_name = 'GERMANY'
)
ORDER BY value DESC;
""",
    },
    {
        "query_id": 12,
        "question": "Determine whether selecting less expensive modes of shipping is negatively affecting the critical-priority orders by causing more parts to be received by customers after the committed date. Count high and low priority orders by ship mode for a given year.",
        "gold_sql": """
SELECT
    l_shipmode,
    SUM(CASE
        WHEN o_orderpriority = '1-URGENT' OR o_orderpriority = '2-HIGH'
        THEN 1 ELSE 0
    END) AS high_line_count,
    SUM(CASE
        WHEN o_orderpriority <> '1-URGENT' AND o_orderpriority <> '2-HIGH'
        THEN 1 ELSE 0
    END) AS low_line_count
FROM orders, lineitem
WHERE o_orderkey = l_orderkey
  AND l_shipmode IN ('MAIL', 'SHIP')
  AND l_commitdate < l_receiptdate
  AND l_shipdate < l_commitdate
  AND l_receiptdate >= DATE '1994-01-01'
  AND l_receiptdate < DATE '1994-01-01' + INTERVAL '1 year'
GROUP BY l_shipmode
ORDER BY l_shipmode;
""",
    },
    {
        "query_id": 13,
        "question": "Determine the distribution of customers by the number of orders they have made, including those who have never placed an order. Seek relationships between the count of orders placed and the characteristics of the customer.",
        "gold_sql": """
SELECT
    c_count,
    COUNT(*) AS custdist
FROM (
    SELECT
        c_custkey,
        COUNT(o_orderkey) AS c_count
    FROM customer LEFT OUTER JOIN orders ON c_custkey = o_custkey
      AND o_comment NOT LIKE '%special%requests%'
    GROUP BY c_custkey
) AS c_orders
GROUP BY c_count
ORDER BY custdist DESC, c_count DESC;
""",
    },
    {
        "query_id": 14,
        "question": "Monitor the market response to a promotion by determining what percentage of revenue in a given month was derived from promotional parts.",
        "gold_sql": """
SELECT
    100.00 * SUM(CASE
        WHEN p_type LIKE 'PROMO%'
        THEN l_extendedprice * (1 - l_discount)
        ELSE 0
    END) / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
FROM lineitem, part
WHERE l_partkey = p_partkey
  AND l_shipdate >= DATE '1995-09-01'
  AND l_shipdate < DATE '1995-09-01' + INTERVAL '1 month';
""",
    },
    {
        "query_id": 15,
        "question": "Find the supplier with the largest total revenue for items shipped in a given quarter. Identify the top revenue supplier.",
        "gold_sql": """
WITH revenue AS (
    SELECT
        l_suppkey AS supplier_no,
        SUM(l_extendedprice * (1 - l_discount)) AS total_revenue
    FROM lineitem
    WHERE l_shipdate >= DATE '1996-01-01'
      AND l_shipdate < DATE '1996-01-01' + INTERVAL '3 months'
    GROUP BY l_suppkey
)
SELECT s_suppkey, s_name, s_address, s_phone, total_revenue
FROM supplier, revenue
WHERE s_suppkey = supplier_no
  AND total_revenue = (SELECT MAX(total_revenue) FROM revenue)
ORDER BY s_suppkey;
""",
    },
    {
        "query_id": 16,
        "question": "Find out how many suppliers can supply parts with given attributes. For parts meeting certain criteria, count the number of distinct suppliers who stock them.",
        "gold_sql": """
SELECT
    p_brand, p_type, p_size,
    COUNT(DISTINCT ps_suppkey) AS supplier_cnt
FROM partsupp, part
WHERE p_partkey = ps_partkey
  AND p_brand <> 'Brand#45'
  AND p_type NOT LIKE 'MEDIUM POLISHED%'
  AND p_size IN (49, 14, 23, 45, 19, 3, 36, 9)
  AND ps_suppkey NOT IN (
      SELECT s_suppkey FROM supplier
      WHERE s_comment LIKE '%Customer%Complaints%'
  )
GROUP BY p_brand, p_type, p_size
ORDER BY supplier_cnt DESC, p_brand, p_type, p_size;
""",
    },
    {
        "query_id": 17,
        "question": "Determine how much average yearly revenue would be lost if orders were no longer filled for small quantities of certain parts. For a given brand and container, find the average loss per year for parts ordered in quantities below 20% of the average.",
        "gold_sql": """
SELECT
    SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23'
  AND p_container = 'MED BOX'
  AND l_quantity < (
      SELECT 0.2 * AVG(l_quantity)
      FROM lineitem
      WHERE l_partkey = p_partkey
  );
""",
    },
    {
        "query_id": 18,
        "question": "Rank customers with an unusually large quantity ordered. Find the top 100 customers who have ever placed a single order with a total quantity over 300.",
        "gold_sql": """
SELECT
    c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice,
    SUM(l_quantity)
FROM customer, orders, lineitem
WHERE o_orderkey IN (
    SELECT l_orderkey
    FROM lineitem
    GROUP BY l_orderkey
    HAVING SUM(l_quantity) > 300
)
  AND c_custkey = o_custkey
  AND o_orderkey = l_orderkey
GROUP BY c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice
ORDER BY o_totalprice DESC, o_orderdate
LIMIT 100;
""",
    },
    {
        "query_id": 19,
        "question": "Find the gross discounted revenue for all orders for three different types of parts shipped by air or delivered in person meeting specific brand, container, and quantity criteria.",
        "gold_sql": """
SELECT
    SUM(l_extendedprice * (1 - l_discount)) AS revenue
FROM lineitem, part
WHERE (
    p_partkey = l_partkey
    AND p_brand = 'Brand#12'
    AND p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
    AND l_quantity >= 1 AND l_quantity <= 1 + 10
    AND p_size BETWEEN 1 AND 5
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
) OR (
    p_partkey = l_partkey
    AND p_brand = 'Brand#23'
    AND p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
    AND l_quantity >= 10 AND l_quantity <= 10 + 10
    AND p_size BETWEEN 1 AND 10
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
) OR (
    p_partkey = l_partkey
    AND p_brand = 'Brand#34'
    AND p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
    AND l_quantity >= 20 AND l_quantity <= 20 + 10
    AND p_size BETWEEN 1 AND 15
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
);
""",
    },
    {
        "query_id": 20,
        "question": "Identify suppliers in a given nation having selected parts that may be candidates for a promotional offer because they have an excess of a given part available. Find suppliers who have more of a given part than 50% of what was shipped in a given year.",
        "gold_sql": """
SELECT s_name, s_address
FROM supplier, nation
WHERE s_suppkey IN (
    SELECT ps_suppkey
    FROM partsupp
    WHERE ps_partkey IN (
        SELECT p_partkey FROM part WHERE p_name LIKE 'forest%'
    )
    AND ps_availqty > (
        SELECT 0.5 * SUM(l_quantity)
        FROM lineitem
        WHERE l_partkey = ps_partkey
          AND l_suppkey = ps_suppkey
          AND l_shipdate >= DATE '1994-01-01'
          AND l_shipdate < DATE '1994-01-01' + INTERVAL '1 year'
    )
)
  AND s_nationkey = n_nationkey
  AND n_name = 'CANADA'
ORDER BY s_name;
""",
    },
    {
        "query_id": 21,
        "question": "Identify suppliers who were not able to ship required parts in a timely manner. Find the ones in a given nation where the supplier was the only one who failed to meet the committed date for a multi-supplier order.",
        "gold_sql": """
SELECT s_name, COUNT(*) AS numwait
FROM supplier, lineitem l1, orders, nation
WHERE s_suppkey = l1.l_suppkey
  AND o_orderkey = l1.l_orderkey
  AND o_orderstatus = 'F'
  AND l1.l_receiptdate > l1.l_commitdate
  AND EXISTS (
      SELECT * FROM lineitem l2
      WHERE l2.l_orderkey = l1.l_orderkey
        AND l2.l_suppkey <> l1.l_suppkey
  )
  AND NOT EXISTS (
      SELECT * FROM lineitem l3
      WHERE l3.l_orderkey = l1.l_orderkey
        AND l3.l_suppkey <> l1.l_suppkey
        AND l3.l_receiptdate > l3.l_commitdate
  )
  AND s_nationkey = n_nationkey
  AND n_name = 'SAUDI ARABIA'
GROUP BY s_name
ORDER BY numwait DESC, s_name
LIMIT 100;
""",
    },
    {
        "query_id": 22,
        "question": "Identify geographies where there are customers who may be likely to make a purchase. Find customers in certain country codes with above-average account balance who have not placed an order in 7 years.",
        "gold_sql": """
SELECT
    cntrycode,
    COUNT(*) AS numcust,
    SUM(c_acctbal) AS totacctbal
FROM (
    SELECT
        SUBSTRING(c_phone FROM 1 FOR 2) AS cntrycode,
        c_acctbal
    FROM customer
    WHERE SUBSTRING(c_phone FROM 1 FOR 2) IN ('13', '31', '23', '29', '30', '18', '17')
      AND c_acctbal > (
          SELECT AVG(c_acctbal)
          FROM customer
          WHERE c_acctbal > 0.00
            AND SUBSTRING(c_phone FROM 1 FOR 2) IN ('13', '31', '23', '29', '30', '18', '17')
      )
      AND NOT EXISTS (
          SELECT * FROM orders WHERE o_custkey = c_custkey
      )
) AS custsale
GROUP BY cntrycode
ORDER BY cntrycode;
""",
    },
]


class TpchLoader:
    """Loads TPC-H benchmark queries."""

    def __init__(self, data_dir: str = "", scale_factor: float = 1.0):
        """
        Args:
            data_dir: Optional path to custom TPC-H query files.
                      If empty, uses built-in queries.
            scale_factor: Scale factor for the TPC-H dataset (default SF1).
        """
        self.data_dir = Path(data_dir) if data_dir else None
        self.scale_factor = scale_factor

    def load_queries(self) -> list[TpchQuery]:
        """
        Load all 22 TPC-H queries with natural language descriptions.

        Returns:
            List of TpchQuery instances, one per TPC-H query.
        """
        queries = []
        for q in TPCH_QUERIES:
            queries.append(
                TpchQuery(
                    query_id=q["query_id"],
                    question=q["question"],
                    gold_sql=q["gold_sql"].strip(),
                    scale_factor=self.scale_factor,
                )
            )
        return queries

    def load_query(self, query_id: int) -> TpchQuery | None:
        """Load a specific TPC-H query by ID (1-22)."""
        for q in TPCH_QUERIES:
            if q["query_id"] == query_id:
                return TpchQuery(
                    query_id=q["query_id"],
                    question=q["question"],
                    gold_sql=q["gold_sql"].strip(),
                    scale_factor=self.scale_factor,
                )
        return None
