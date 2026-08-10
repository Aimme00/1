from __future__ import annotations

import io
import math
import re
import zipfile
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence
from xml.sax.saxutils import escape


def export_xlsx(result: Dict[str, Any]) -> bytes:
    """生成无外部运行依赖的 OOXML 报表：概览、数据明细、SQL 三个工作表。"""
    sheets = [
        ("分析概览", _summary_sheet(result)),
        ("数据明细", _data_sheet(result)),
        ("SQL", _sql_sheet(result)),
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _root_relationships())
        archive.writestr("docProps/core.xml", _core_properties())
        archive.writestr("docProps/app.xml", _app_properties([name for name, _ in sheets]))
        archive.writestr("xl/workbook.xml", _workbook_xml([name for name, _ in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships(len(sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, (_, xml) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
    return output.getvalue()


def _summary_sheet(result: Dict[str, Any]) -> str:
    scope = result.get("scope") or {}
    insights = list(result.get("insights") or [])
    warnings = list(result.get("warnings") or [])
    rows: List[Sequence[Any]] = [
        ["数据分析报告"],
        [],
        ["用户问题", scope.get("question") or ""],
        ["分析结论", result.get("answer") or ""],
        [],
        ["数据库", scope.get("database") or "", "返回行数", scope.get("row_count")],
        ["运行 ID", result.get("run_id") or "", "图表已请求", "是" if scope.get("chart_requested") else "否"],
        ["生成时间", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
        [],
        ["关键洞察", "说明"],
    ]
    for insight in insights:
        if isinstance(insight, dict):
            rows.append([insight.get("title") or insight.get("type") or "洞察", insight.get("text") or ""])
    if not insights:
        rows.append(["暂无", "本次结果未生成额外洞察。"])
    rows.extend([[], ["风险与提示"]])
    if warnings:
        rows.extend([[f"• {warning}"] for warning in warnings])
    else:
        rows.append(["本次分析无额外警告。"])

    styles = {(1, 1): 1, (10, 1): 2, (10, 2): 2}
    merges = ["A1:F1", "B3:F3", "B4:F4", "B8:F8"]
    for row_index in range(3, len(rows) + 1):
        styles.setdefault((row_index, 1), 3 if row_index in (10,) else 4)
        styles.setdefault((row_index, 2), 4)
    insight_count = len(insights) or 1
    warning_header = 12 + insight_count
    styles[(warning_header, 1)] = 2
    row_heights = {1: 32, 4: 64}
    for row_index in range(11, 11 + insight_count):
        row_heights[row_index] = 42
    for row_index in range(warning_header + 1, len(rows) + 1):
        row_heights[row_index] = 54
        merges.append(f"A{row_index}:F{row_index}")
    return _worksheet_xml(
        rows,
        widths=[22, 34, 18, 18, 14, 14],
        styles=styles,
        merges=merges,
        freeze_rows=0,
        row_heights=row_heights,
    )


def _data_sheet(result: Dict[str, Any]) -> str:
    table = result.get("table") or {}
    columns = list(table.get("columns") or [])
    source_rows = list(table.get("rows") or [])
    rows: List[Sequence[Any]] = [columns]
    for row in source_rows:
        if isinstance(row, dict):
            rows.append([row.get(column) for column in columns])
        elif isinstance(row, (list, tuple)):
            rows.append(list(row))
    styles = {(1, column_index): 3 for column_index in range(1, len(columns) + 1)}
    for row_index, row in enumerate(rows[1:], start=2):
        for column_index, value in enumerate(row, start=1):
            if isinstance(value, bool):
                styles[(row_index, column_index)] = 4
            elif isinstance(value, int):
                styles[(row_index, column_index)] = 6
            elif isinstance(value, float):
                styles[(row_index, column_index)] = 5
            else:
                styles[(row_index, column_index)] = 4
    widths = [_column_width(column, rows[1:], index) for index, column in enumerate(columns)]
    auto_filter = f"A1:{_column_letter(max(1, len(columns)))}{max(1, len(rows))}" if columns else None
    return _worksheet_xml(
        rows,
        widths=widths or [18],
        styles=styles,
        freeze_rows=1,
        auto_filter=auto_filter,
    )


def _sql_sheet(result: Dict[str, Any]) -> str:
    sql = result.get("sql") or {}
    validation = sql.get("validation") or {}
    rows = [
        ["SQL 查询与审计信息"],
        [],
        [sql.get("text") or "-- 本次未执行 SQL"],
        [],
        ["方言", sql.get("dialect") or "", "执行耗时（ms）", sql.get("duration_ms") or 0],
        ["校验器", validation.get("parser") or "", "校验通过", "是" if validation.get("is_valid") else "否"],
        [],
        ["说明"],
        ["SQL 由系统生成，仅供核验。生产环境必须使用只读数据库账号与 AST 安全校验。"],
    ]
    styles = {(1, 1): 1, (3, 1): 7, (8, 1): 2, (9, 1): 4}
    return _worksheet_xml(
        rows,
        widths=[24, 22, 22, 18, 16, 16, 16, 16],
        styles=styles,
        merges=["A1:H1", "A3:H3", "A9:H9"],
        row_heights={1: 32, 3: 120, 9: 42},
    )


def _worksheet_xml(
    rows: Sequence[Sequence[Any]],
    *,
    widths: Sequence[float],
    styles: Dict[tuple[int, int], int],
    merges: Sequence[str] = (),
    freeze_rows: int = 0,
    auto_filter: str | None = None,
    row_heights: Dict[int, float] | None = None,
) -> str:
    row_heights = row_heights or {}
    columns_xml = "".join(
        f'<col min="{index}" max="{index}" width="{max(8, min(width, 48)):.1f}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    sheet_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(values, start=1):
            if value is None:
                continue
            reference = f"{_column_letter(column_index)}{row_index}"
            style = styles.get((row_index, column_index), 0)
            cells.append(_cell_xml(reference, value, style))
        height = row_heights.get(row_index)
        height_attr = f' ht="{height}" customHeight="1"' if height else ""
        sheet_rows.append(f'<row r="{row_index}"{height_attr}>{"".join(cells)}</row>')
    pane = ""
    if freeze_rows:
        pane = (
            f'<pane ySplit="{freeze_rows}" topLeftCell="A{freeze_rows + 1}" '
            'activePane="bottomLeft" state="frozen"/>'
        )
    merge_xml = ""
    if merges:
        merge_xml = f'<mergeCells count="{len(merges)}">' + "".join(
            f'<mergeCell ref="{reference}"/>' for reference in merges
        ) + "</mergeCells>"
    filter_xml = f'<autoFilter ref="{auto_filter}"/>' if auto_filter else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetViews><sheetView workbookViewId="0" showGridLines="0">{pane}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        f'<cols>{columns_xml}</cols><sheetData>{"".join(sheet_rows)}</sheetData>'
        f'{filter_xml}{merge_xml}<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.2" footer="0.2"/>'
        '</worksheet>'
    )


def _cell_xml(reference: str, value: Any, style: int) -> str:
    if isinstance(value, bool):
        return f'<c r="{reference}" s="{style}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    text = escape(str(value), {'"': '&quot;'})
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _column_width(column: str, rows: Sequence[Sequence[Any]], index: int) -> float:
    values = [str(column)]
    for row in rows[:200]:
        if index < len(row) and row[index] is not None:
            values.append(str(row[index]))
    longest = max((len(re.sub(r"[^\x00-\xff]", "xx", value)) for value in values), default=10)
    return max(12, min(longest + 3, 32))


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _content_types(sheet_count: int) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f'{sheet_overrides}</Types>'
    )


def _root_relationships() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )


def _core_properties() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>AskData 数据分析报告</dc:title><dc:creator>AskData Agent</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>'
        '</cp:coreProperties>'
    )


def _app_properties(sheet_names: Iterable[str]) -> str:
    names = list(sheet_names)
    titles = "".join(f'<vt:lpstr>{escape(name)}</vt:lpstr>' for name in names)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>AskData Agent</Application><HeadingPairs><vt:vector size="2" baseType="variant">'
        '<vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>'
        f'<vt:variant><vt:i4>{len(names)}</vt:i4></vt:variant></vt:vector></HeadingPairs>'
        f'<TitlesOfParts><vt:vector size="{len(names)}" baseType="lpstr">{titles}</vt:vector></TitlesOfParts>'
        '</Properties>'
    )


def _workbook_xml(sheet_names: Sequence[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets}</sheets><calcPr calcId="191029"/></workbook>'
    )


def _workbook_relationships(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    relationships += (
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{relationships}</Relationships>'
    )


def _styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>
  <fonts count="5">
    <font><sz val="11"/><name val="Aptos"/><family val="2"/></font>
    <font><b/><sz val="20"/><color rgb="FFFFFFFF"/><name val="Aptos Display"/></font>
    <font><b/><sz val="12"/><color rgb="FF172033"/><name val="Aptos"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>
    <font><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF4159DC"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEFF2FF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF253049"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left/><right/><top/><bottom style="thin"><color rgb="FFD9DEE8"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="4" borderId="0" xfId="0"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"><alignment horizontal="right"/></xf>
    <xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"><alignment horizontal="right"/></xf>
    <xf numFmtId="0" fontId="4" fillId="4" borderId="0" xfId="0"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
