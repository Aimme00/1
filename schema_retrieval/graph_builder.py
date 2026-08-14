from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Set

from .objects import ColumnSchema, SchemaGraph, SchemaHit, TableRelation, TableSchema


def build_schema_graph(
    hits: List[SchemaHit],
    tables: Dict[str, TableSchema],
    all_columns: List[ColumnSchema],
    relations: List[TableRelation],
    include_join_columns: bool = True,
) -> SchemaGraph:
    """
    根据检索命中的字段构建 Schema 子图。

    输入：
    - hits: 检索命中的字段。
    - tables: 全量表 Schema。
    - all_columns: 全量字段 Schema。
    - relations: 全量表关系。

    输出：
    - 仅包含相关表、相关字段、相关关联关系的 SchemaGraph。
    """

    selected_tables = {hit.column.table_name for hit in hits}

    # 将检索命中的表通过最短关联路径连成可执行子图。复杂分析往往只直接命中
    # 两端维度（例如 customers.region 与 products.product_name），但 SQL 必须
    # 经过 orders、order_items 才能正确 JOIN。缺少中间表会导致模型生成了合法
    # 业务 SQL，却在字段白名单阶段被误判为越权。
    selected_tables.update(
        _relation_path_closure(selected_tables, relations)
    )

    selected_columns = defaultdict(dict)

    for hit in hits:
        col = hit.column
        selected_columns[col.table_name][col.column_name] = col

    selected_relations: List[TableRelation] = []

    for relation in relations:
        source_selected = relation.source_table in selected_tables
        target_selected = relation.target_table in selected_tables

        if source_selected and target_selected:
            selected_relations.append(relation)

            if include_join_columns:
                source_col = _find_column(
                    all_columns,
                    relation.source_table,
                    relation.source_column,
                )
                target_col = _find_column(
                    all_columns,
                    relation.target_table,
                    relation.target_column,
                )

                if source_col:
                    selected_columns[relation.source_table][relation.source_column] = source_col

                if target_col:
                    selected_columns[relation.target_table][relation.target_column] = target_col

    graph_tables = {
        table_name: tables[table_name]
        for table_name in selected_tables
        if table_name in tables
    }

    # 字段级召回只负责确定“相关表”，不能直接充当 SQL 字段权限边界。
    # SQL 中经常还需要过滤字段、维度字段和排序字段；只保留直接命中的字段会
    # 让合法 SQL 在严格 AST 校验时被误判为越权。相关表确定后带齐该表字段，
    # 仍由表白名单把查询范围限制在本次 Schema 子图内。
    graph_columns = {
        table_name: [
            column
            for column in all_columns
            if column.table_name == table_name
        ]
        for table_name in graph_tables
    }

    database = ""
    if graph_tables:
        database = next(iter(graph_tables.values())).database

    return SchemaGraph(
        database=database,
        tables=graph_tables,
        columns=graph_columns,
        relations=selected_relations,
    )


def _relation_path_closure(
    selected_tables: Set[str],
    relations: List[TableRelation],
) -> Set[str]:
    """返回连接已命中表所需的最短路径中间表。"""
    if len(selected_tables) < 2:
        return set()

    adjacency: Dict[str, Set[str]] = defaultdict(set)
    for relation in relations:
        adjacency[relation.source_table].add(relation.target_table)
        adjacency[relation.target_table].add(relation.source_table)

    anchors = sorted(selected_tables)
    closure: Set[str] = set(selected_tables)
    root = anchors[0]

    for target in anchors[1:]:
        queue = deque([(root, [root])])
        visited = {root}
        path: List[str] = []
        while queue:
            current, current_path = queue.popleft()
            if current == target:
                path = current_path
                break
            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_path + [neighbor]))
        closure.update(path)

    return closure


def _find_column(
    columns: List[ColumnSchema],
    table_name: str,
    column_name: str,
) -> ColumnSchema | None:
    for column in columns:
        if column.table_name == table_name and column.column_name == column_name:
            return column

    return None
