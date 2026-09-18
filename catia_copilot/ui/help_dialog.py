"""
帮助对话框 – 在可滚动的富文本窗口中显示用户文档。
"""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from catia_copilot.constants import (
    APP_AUTHOR,
    APP_CONTACT,
    APP_NAME,
    APP_VERSION,
    MAX_INERTIA_INDEX,
)
from catia_copilot.i18n import LANG_EN_US, current_ui_language, translate
from catia_copilot.utils import resource_path

_HELP_HTML = f"""\
<h2>{APP_NAME} v{APP_VERSION} — 帮助文档</h2>

<h3>概述</h3>
<p>
  {APP_NAME} 是一款面向工程团队的 CATIA V5 辅助工具，旨在简化日常操作，提升工作效率。
  支持图纸与零件的批量导出、 BOM 管理、质量特性统计、宏脚本快捷运行，以及 CATIA 资源文件的一键部署。
</p>

<hr />
<h3>运行环境要求</h3>
<ul>
  <li>操作系统： Windows 10 / 11</li>
  <li>已安装 CATIA V5 R28（文件导出等功能需要 CATIA 处于运行状态）</li>
</ul>

<hr />
<h3>功能说明</h3>

<h4>一、导出</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>CATDrawing → PDF</b></td>
    <td>
      批量将 CATDrawing 文件导出为 PDF 。<br />
      支持为输出文件添加自定义前缀（默认 DR_）。<br />
      <i>注意：多页图纸请在 CATIA 中设置"将多页文档保存在单向量文件中" （工具 → 选项 → 常规 → 兼容性
        → 图形格式 → 导出）。</i>
    </td>
  </tr>
  <tr>
    <td><b>CATPart / CATProduct → STP</b></td>
    <td>
      批量将 CATPart 或 CATProduct 文件导出为 STEP 格式。<br />
      支持为输出文件添加自定义前缀（默认 MD_）。
    </td>
  </tr>
  <tr>
    <td><b>从 CATProduct 导出 BOM</b></td>
    <td>
      从当前打开的 CATProduct 文件中提取完整 BOM 信息，并导出至 Excel (.xlsx)。<br />
      可选择需要包含的列、自定义列、以及导出层级。
    </td>
  </tr>
</table>

<h4>二、编辑</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>BOM 属性补全</b></td>
    <td>
      加载当前 CATProduct 的 BOM 属性到表格中，可直接编辑零件编号、术语、
      定义、版本、来源等字段，以及自定义的用户属性（物料编码、物料名称、
      规格型号等）。修改完成后可一键写回 CATIA 。<br />
      <i>同一文件的属性修改会自动联动更新。</i><br /><br />
      <b>主要操作功能：</b>
      <ul>
        <li><b>脏字段高亮</b>：已修改的单元格以橙色粗体标识；悬停可查看修改前的原始值。</li>
        <li><b>撤销/重做</b>（Ctrl+Z / Ctrl+Y）：支持最多 10 步撤销与重做。</li>
        <li><b>搜索过滤</b>（Ctrl+F）：在搜索框中输入关键字，实时过滤表格行。</li>
        <li><b>表头点击排序</b>：点击任意列标题升/降序排序。</li>
        <li><b>保存并写回</b>（Ctrl+S）：一键将修改写回 CATIA 。关闭时若有未保存修改会弹出确认。</li>
        <li><b>右键菜单</b>：复制单元格内容、打开文件所在路径等。</li>
        <li><b>导出 Excel</b>：导出后弹窗提供"打开文件"和"打开所在文件夹"快捷按钮。</li>
        <li><b>窗口位置记忆</b>：对话框的尺寸和位置在关闭后自动保存、重启后恢复。</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>重量、重心、惯量统计</b></td>
    <td>
      遍历当前 CATProduct 的产品树，读取每个零件的质量（重量）、重心坐标及转动惯量，
      在根产品坐标系下按装配层级自动汇总，并支持导出至 Excel 。<br />
      支持两种数据来源：
      <ul>
        <li><b>Analyze 模式</b>（默认）：通过 CATIA Analyze API 实时计算，零件已赋材料即可使用，无需额外操作。</li>
        <li><b>惯量包络体模式</b>：读取 SPA「测量惯量 + 保持测量」写入的参数，需提前为每个零件执行保持测量。详见下方专项说明。</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>新建图纸</b></td>
    <td>
      根据 drawing_templates 文件夹中的 CATDrawing 模板，在 CATIA 中为当前 活动的 CATPart 或
      CATProduct 生成新图纸。<br />
      <i>需在 CATIA 中打开目标零件/产品，并将 *.CATDrawing 模板放入 drawing_templates 文件夹。</i>
    </td>
  </tr>
  <tr>
    <td><b>刷新图纸</b></td>
    <td>
      将 CATIA 中当前活动 CATDrawing 图纸的参数（零件编号、术语、版本及
      自定义属性）与对应的零件/产品同步刷新。<br />
      <i>需在 CATIA 中同时打开目标图纸和对应零件/产品文档。</i>
    </td>
  </tr>
</table>

<hr />
<h3>重量、重心、惯量统计——详细说明</h3>

<h4>1. 功能概述</h4>
<p>
  本功能从产品树中读取每个零件的质量（kg）、重心坐标（mm）和转动惯量张量（kg·m²），
  将所有数据统一变换到<b>根产品坐标系</b>，再按装配层级逐级汇总，最终展示整棵产品树的
  完整质量特性，并可导出至 Excel 。
</p>
<p>
  支持两种数据来源，通过界面顶部的<b>「数据来源」</b>单选按钮切换：
</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Analyze</b>（默认）</td>
    <td>
      通过 pycatia Analyze API（<code>product.analyze</code>）实时向 CATIA 计算引擎
      请求质量特性——无需在 CATIA 中手动创建任何保持测量。<br />
      <b>前提：</b>零件已赋材料（CATIA 材料库或手动设置密度），CATIA 才能计算出非零质量。<br />
      选中此模式时，「惯量包络体读取」选项自动置灰不可用。
    </td>
  </tr>
  <tr>
    <td><b>惯量包络体</b></td>
    <td>
      读取 CATIA SPA 工具栏「测量惯量 + 保持测量」写入到参数树中的
      <code>惯量包络体.1</code>…<code>惯量包络体.N</code> 保持测量参数。<br />
      <b>前提：</b>需用户预先为每个零件执行保持测量操作（见第 2 节）。<br />
      支持多个惯量包络体的读取模式（只读.1 / 最大编号 / 全部汇总），适用于多材料零件。
    </td>
  </tr>
</table>
<p>
  所有内部计算均使用<b>国际单位制（SI）</b>：质量 kg、坐标 mm、转动惯量 kg·mm²。
  界面显示和导出时根据用户选择的单位换算为实用单位（g/kg、mm/m、g·mm²/g·m²/kg·mm²/kg·m²）。
</p>

<h4>2. 数据来源：Analyze 模式详解</h4>
<p>
  Analyze 模式直接调用 CATIA 的计算引擎，不依赖任何手动预操作。具体机制如下：
</p>
<ul>
  <li>
    程序获取零件文档的根 Product（<code>ReferenceProduct.Parent.Product</code>）上的
    <code>analyze</code> 对象，调用：
    <ul>
      <li><code>analyze.mass</code> → 零件质量（kg）</li>
      <li><code>analyze.get_gravity_center()</code> → 零件局部坐标系下的重心坐标（mm）</li>
      <li><code>analyze.get_inertia()</code> → 关于重心的转动惯量张量（kg·mm²，9 元素行主序）</li>
      <li><code>analyze.volume</code> → 体积（mm³），用于推算密度（kg/m³）</li>
    </ul>
  </li>
  <li>
    <b>坐标系语义</b>与惯量包络体模式完全一致：返回值均为<b>零件局部坐标系</b>下的结果，
    程序统一变换到根产品坐标系后再汇总，装配位姿完全由 CATIA Position 矩阵决定。
  </li>
  <li>
    <b>若零件未赋材料</b>，CATIA 返回 <code>mass == 0</code>，程序将该零件标记为"—"（未读取到质量特性），
    与惯量包络体模式下未找到保持测量的显示效果相同。
  </li>
  <li>
    <b>密度</b>由 <code>mass / volume</code> 计算得到；若体积读取失败则密度显示"—"（不可编辑）。
  </li>
</ul>
<p><b>Analyze 模式不支持</b>多惯量包络体（多材料分区）合并——每个零件只能获得一个整体质量特性。
若需要多材料分区分别测量再汇总，请使用惯量包络体模式的「全部汇总」读取模式。</p>

<h4>3. 数据来源：惯量包络体模式前提条件（保持测量）</h4>
<p>
  <b>仅在使用「惯量包络体」数据来源时</b>，才需要为产品树中每个零件完成以下操作：
</p>
<ol>
  <li>
    <b>单独打开零件文件</b>（CATPart），不要在产品窗口中操作。<br />
    <i>原因：在产品窗口下建立的测量，其参考坐标系为根产品坐标系，而非零件自身坐标系。
      本功能期望重心坐标在零件局部坐标系下给出，再由软件统一变换到根产品坐标系；
      若测量时已采用根产品坐标系，坐标系不重合的零件计算结果将出错。</i>
  </li>
  <li>执行菜单 <b>测量 → 测量惯量</b>（Measure Inertia）。</li>
  <li>在对话框中勾选 <b>"保持测量"（Keep Measure）</b>，点击确定。</li>
  <li>
    CATIA 将在参数树中生成名为 <code>惯量包络体.1</code>的几何体， 其下包含以下参数：<br />
    <code>质量</code>（kg）、<code>密度</code>（kg/m³）、<code>Gx / Gy / Gz</code>（重心坐标，mm）、
    <code>IoxG / IoyG / IozG / IxyG / IxzG / IyzG</code>（惯量分量，kg·m²）
  </li>
  <li>
    如需为同一零件记录多次测量（例如包含不同材料的几何体），可重复以上步骤， CATIA 会依次生成
    <code>惯量包络体.2</code>、<code>惯量包络体.3</code>…… 本功能最多读取编号 1 到 {MAX_INERTIA_INDEX} 的保持测量。
  </li>
</ol>
<p>
  <b>重要：自定义测量参数的最小勾选要求</b><br />
  CATIA 的"测量惯量"对话框支持通过"自定义…"按钮选择保存哪些参数。
  本功能需要读取以下参数，<b>请确保这些项目均已勾选</b>，否则惯量包络体中将缺少必要数据导致读取失败：
</p>
<ul>
  <li>密度/表面质量</li>
  <li>质量</li>
  <li>重心 (G)</li>
  <li>重心惯量矩阵</li>
</ul>
<p>下图为"自定义测量"对话框中所需勾选的最小范围示意（勾选项目少于此范围则无法正常读取）：</p>
<p>
  <img src="inertia_keep_params.png" alt="惯量保持测量最小参数示意图" style="max-width: 480px" />
</p>
<p><b>提示：</b>测量惯量生成的惯量包络体须手动更新，全局更新不会更新惯量包络体。</p>

<h4>4. 打开方式与数据来源</h4>
<p>点击菜单 <b>编辑 → 重量、重心、惯量统计</b>，打开统计对话框。有两种加载数据的方式：</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>从 CATIA 现场加载</b></td>
    <td>
      <ul>
        <li>
          勾选 <b>"使用当前 CATIA 活动文档"</b>：直接读取 CATIA 中当前处于激活状态的 CATProduct 。
        </li>
        <li>
          或点击 <b>"浏览…"</b> 选择磁盘上的 CATProduct 文件，再点击 <b>"加载"</b>。 CATIA
          会自动打开该文件（如尚未打开）后进行遍历。
        </li>
      </ul>
      加载过程中会显示进度提示，对于大型产品可能需要数分钟。
    </td>
  </tr>
  <tr>
    <td><b>从已保存数据载入</b></td>
    <td>
      点击 <b>"载入已保存数据…"</b>，选择之前通过"保存数据"按钮保存的 <code>.mpd</code> 数据文件。
      这种方式无需打开 CATIA ，适合离线分析或数据共享。
    </td>
  </tr>
  <tr>
    <td><b>追加数据… / 追加活动文档…</b></td>
    <td>
      在已加载基础产品数据后，可将额外的分总成质量特性追加合并，适用于主产品过大、需分批采集各分总成的场景。
      <ul>
        <li>
          <b>追加数据…</b>：从一个或多个
          <code>.mpd</code> 数据文件中追加分总成质量特性（支持多文件同时选取）。
        </li>
        <li>
          <b>追加活动文档…</b>：将 CATIA 当前活动文档（分总成
          CATProduct）的质量特性实时追加到现有数据中。
        </li>
      </ul>
      <b>前提：各分总成的坐标系须与主产品（及彼此）一致，程序不执行额外坐标变换。</b><br />
      两个按钮在加载基础数据后方才启用；若当前尚无数据，操作会被提示并阻止。
    </td>
  </tr>
</table>

<h4>5. 读取与显示选项</h4>

<p><b>5.1 数据来源</b>（界面顶部单选按钮）</p>
<p>见第 1 节功能概述，切换 Analyze / 惯量包络体 两种模式。选择 Analyze 时，以下「惯量包络体读取」选项自动置灰。</p>

<p><b>5.2 惯量包络体读取模式</b>（仅「惯量包络体」来源时有效，对加载性能有影响）</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>只读.1</b></td>
    <td>
      仅读取每个零件的 <code>惯量包络体.1</code>。速度最快：每个零件只进行一次参数查询。
      适用于每个零件只有一个保持测量的常规情况。
    </td>
  </tr>
  <tr>
    <td><b>最大编号</b></td>
    <td>
      扫描编号 1 到 20 的全部惯量包络体，取编号最大的有效保持测量结果。
      适用于需要以"最新一次测量"为准的场景（例如重复测量后保留最后一次）。
      速度较慢：每个不存在的编号均会产生一次 COM 异常，最多产生 19 次。
    </td>
  </tr>
  <tr>
    <td><b>全部汇总</b>（默认）</td>
    <td>
      读取全部有效编号（1–20），按<b>平行轴定理</b>在零件级汇总为单一质量特性。
      适用于一个零件有多个分区域测量（如多材料零件各区域分别建立惯量包络体）的场景。
      速度与"最大编号"模式相同。
    </td>
  </tr>
</table>

<p><b>5.3 BOM 展示模式</b></p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>层级 BOM</b>（默认）</td>
    <td>
      以树形结构展示完整产品树，包含零件（叶节点）和产品/部件（中间节点）。
      产品/部件行显示其子树内所有零件的<b>汇总值</b>（在根产品坐标系下）。 零件行的 Weight /
      CogX/Y/Z / Ixx–Iyz 均已变换到<b>根产品坐标系</b>，与装配位置有关。
    </td>
  </tr>
  <tr>
    <td><b>汇总 BOM</b></td>
    <td>
      按零件编号合并相同零件，每个唯一零件编号（Part Number）显示为一行，
      并显示数量（Quantity）。仅列出零件（不含产品和部件节点）。 Weight / CogX/Y/Z / Ixx–Iyz
      在<b>零件自身坐标系</b>下显示，与装配位置无关。
      可选择排序列（零件编号、术语、版本、文件名、数量、重量等）对行进行排序。
    </td>
  </tr>
</table>

<p><b>5.4 单位设置</b></p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>重量单位</b></td>
    <td>g 或 kg（默认 g）。</td>
  </tr>
  <tr>
    <td><b>长度单位（重心坐标）</b></td>
    <td>mm 或 m（默认 mm）。</td>
  </tr>
  <tr>
    <td><b>惯量单位</b></td>
    <td>g·mm²、g·m²、kg·mm² 或 kg·m²（默认 g·mm²）。惯量单位独立于重量和长度单位单独选择。</td>
  </tr>
</table>
<p>切换单位时表格实时更新，列标题自动加上当前单位后缀，所有设置跨会话保持不变。</p>

<p><b>5.5 可选显示列</b></p>
<p>通过"显示列"区域的复选框，可以显示或隐藏以下列：</p>
<ul>
  <li><b>文件名（Filename）</b>：零件对应的磁盘文件名。</li>
  <li><b>零件编号（Part Number）</b>： CATIA 中的 PartNumber 属性。</li>
  <li><b>术语（Nomenclature）</b>： CATIA 中的 Nomenclature 属性。</li>
  <li><b>版本（Revision）</b>： CATIA 中的 Revision 属性。</li>
</ul>

<p><b>5.6 忽略隐藏的节点</b></p>
<p>
  勾选后：零件处于隐藏状态（不可见）则跳过；产品/部件处于隐藏状态则连同其子孙一并跳过， 不纳入统计。
</p>

<h4>6. 表格操作</h4>

<p>
  表格支持<b>多选</b>（Ctrl＋单击 或 Shift＋单击）。多选时通过右键上下文菜单可对选中的所有行
  执行批量操作（批量删除、批量切换参与计算、批量重新读取）。若右键单击的行不在当前选中集中，
  程序会清除原有选中并仅选中该行（退化为单选）。
</p>

<p><b>6.1 编辑重量</b></p>
<p>
  双击零件行的"重量"单元格可直接输入修改值（仅零件行可编辑，产品/部件汇总行不可编辑）。 修改后：
</p>
<ul>
  <li>按相同比例等比缩放该零件的全部惯量分量（保持质量-惯量关系一致）。</li>
  <li>同步更新所有相同零件编号（Part Number）的其他行。</li>
  <li>自动重新计算产品总质量特性，底部"汇总结果"面板实时刷新。</li>
  <li>重量修改同时等比更新密度（体积不变，密度 = 质量 / 体积）。</li>
</ul>
<p>"重量"单元格不允许输入 0 和负数。</p>

<p><b>6.2 编辑密度</b></p>
<p>
  双击零件行的"密度"单元格可直接修改密度值（kg/m³）。修改后按比例缩放该零件的重量和惯量
  （体积不变，质量 = 密度 × 体积）。以下情况密度列不可编辑：
</p>
<ul>
  <li>密度值为 "—"（原始测量中无密度数据）。</li>
  <li>密度值为 -1（CATIA 报告该零件的材料不统一，无法给出单一密度）。</li>
</ul>

<p><b>6.3 右键上下文菜单</b></p>
<p>右键单击表格中的任意行，可弹出如下菜单（带"仅单选"标注的项目在多选时不可用）：</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>打开路径</b></td>
    <td>（仅单选）在文件资源管理器中打开该文件所在的文件夹。</td>
  </tr>
  <tr>
    <td><b>复制路径</b></td>
    <td>（仅单选）将该行对应文件的完整路径复制到剪贴板。</td>
  </tr>
  <tr>
    <td><b>在 CATIA 中打开</b></td>
    <td>（仅单选）通过系统关联程序在 CATIA 中打开该零件/产品文件。</td>
  </tr>
  <tr>
    <td><b>重新读取质量特性</b></td>
    <td>
      （仅零件行可用）在不重新遍历产品树的情况下，从 CATIA 中重新读取该零件的
      惯量包络体保持测量参数，并同步更新所有相同零件编号的节点以及汇总结果。 适用于在 CATIA
      中更新了零件测量后快速刷新单个零件的场景。多选时对所有选中的零件行批量执行。
    </td>
  </tr>
  <tr>
    <td><b>增加对称件</b></td>
    <td>
      （层级 BOM 模式，仅单选）为选中行新增一条虚拟的"对称件"行，位于选中行正上方，以浅蓝色背景标识。
      对称规则：相对 ZX 平面对称（即 CogY、Ixy、Iyz 取反，其余分量不变）。
      对称件行参与汇总计算，但不可直接编辑质量/密度；当源行被修改或删除时，对称件行自动同步或级联删除。
      详见第 5.5 节。
    </td>
  </tr>
  <tr>
    <td><b>参与计算：√ / ×</b></td>
    <td>
      （层级 BOM 模式）切换该行是否参与质量汇总计算。排除后该行以灰色斜体显示；
      同一零件编号的所有实例同步排除/恢复。排除行不计入底部汇总，也不出现在汇总 BOM 中。
      多选时：若所有选中行均已排除，则全部恢复参与；否则将全部行标记为排除。
    </td>
  </tr>
  <tr>
    <td><b>从列表删除</b></td>
    <td>
      （层级 BOM 模式）将该行及其全部子孙行从显示列表中移除（不影响 CATIA 文件）。
      删除前会弹出确认对话框以防误操作。若该行有关联对称件，对称件行同时级联删除。
      多选时对所有选中行及其子树批量执行。
    </td>
  </tr>
</table>

<p><b>6.4 展开 / 折叠 / 自适应列宽</b></p>
<p>
  底部按钮区提供"全部展开"、"全部折叠"、"自适应列宽"按钮，方便浏览大型装配树。
  列宽可手动拖拽调整，切换模式后保留已调整的列宽。
  重新加载或追加数据后，程序自动恢复加载前的节点展开/折叠状态。
</p>

<p><b>6.5 对称件（虚拟行）功能</b></p>
<p>
  在层级 BOM 模式下，右键选中任意行（零件、产品或部件），点击 <b>"增加对称件"</b>，
  即可在该行正上方插入一条<b>虚拟对称件行</b>。对称件行以<b>浅蓝色背景</b>显示，类型列显示"对称件"。
</p>
<p>对称规则（相对 ZX 平面镜像，即 Y 轴取反）：</p>
<ul>
  <li>重心：CogX、CogZ 不变，CogY → −CogY（根坐标系）</li>
  <li>转动惯量：Ixx、Iyy、Izz、Ixz 不变，Ixy → −Ixy，Iyz → −Iyz（根坐标系）</li>
  <li>质量（重量）不变</li>
</ul>
<p>使用说明：</p>
<ul>
  <li>对称件行<b>参与汇总计算</b>，其质量特性贡献与源行同等对待。</li>
  <li>对称件行<b>不可直接编辑</b>质量或密度；若需修改，请编辑源行，对称件行将自动同步更新。</li>
  <li><b>级联删除</b>：删除源行时，关联的对称件行会一并删除。</li>
  <li>对称件行同样支持"参与计算 √/×"切换。</li>
  <li>对称件行会出现在导出的 Excel 中，Status 列标注 mirror 标记。</li>
</ul>

<h4>7. 汇总结果面板</h4>
<p>
  表格下方的"汇总结果（基于根产品坐标系）"面板，展示当前产品树中
  所有<b>未被排除</b>的有效零件，在根产品坐标系下汇总后的结果。面板分为三列：
</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>左列：总重量 + 重心 (G)</b></td>
    <td>
      <ul>
        <li><b>总重量</b>：以当前所选重量单位（g 或 kg）显示。</li>
        <li><b>重心 Gx / Gy / Gz</b>：以当前所选长度单位（mm 或 m）显示。</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>中列：惯量矩阵</b></td>
    <td>
      以 3×3 完整矩阵形式展示关于重心的转动惯量张量：<br />
      Ixx、Ixy、Ixz<br />
      Iyx、Iyy、Iyz<br />
      Izx、Izy、Izz<br />
      （以当前所选惯量单位显示；矩阵对称，Ixy=Iyx 等）
    </td>
  </tr>
  <tr>
    <td><b>右列：重心主惯量矩 + 主轴</b></td>
    <td>
      对惯量矩阵进行特征值分解，给出：
      <ul>
        <li><b>重心主惯量矩 M1 / M2 / M3</b>：三个主惯量矩（按升序排列，以当前惯量单位显示）。</li>
        <li>
          <b>主轴 A1 / A2 / A3</b>：对应主惯量矩的单位方向向量（各给出 x / y / z 分量，无量纲）。
        </li>
      </ul>
    </td>
  </tr>
</table>
<p>每次修改重量/密度、切换排除状态或追加数据后，该面板自动刷新。</p>

<h4>8. 导出与保存</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>导出表格</b></td>
    <td>
      将当前表格（含底部汇总行）导出为 Excel （.xlsx）文件。 导出内容：所有当前可见列（不含内部序号列
      #），末尾追加 Status（状态）列。<br />
      排除行以特殊背景色标记；测量失败的零件行以橙色标记； 数值均以当前所选单位导出。
    </td>
  </tr>
  <tr>
    <td><b>保存数据…</b></td>
    <td>
      将当前行数据保存为 <code>.mpd</code> 压缩二进制文件（内容为 gzip 压缩的 JSON）。
      保存后可随时通过"载入已保存数据…"在不连接 CATIA 的情况下重新打开。
    </td>
  </tr>
</table>

<h4>9. 常见错误状态说明</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>— （破折号）</b></td>
    <td>
      质量特性读取失败。原因视数据来源模式不同：<br />
      <b>Analyze 模式：</b>零件未赋材料或为曲面体，CATIA 返回质量为 0。请在 CATIA 中为零件赋材料后，右键"重新读取质量特性"。<br />
      <b>惯量包络体模式：</b>零件未执行"保持测量"，或 CATIA 参数树中找不到 <code>惯量包络体.x</code>。请<b>单独打开</b>该零件，执行测量后重新加载。
    </td>
  </tr>
  <tr>
    <td><b>文件未找到</b></td>
    <td>磁盘上找不到该零件文件，可能已被移动或重命名。无法进行质量特性读取。</td>
  </tr>
  <tr>
    <td><b>密度显示 —</b></td>
    <td>CATIA 测量时未读取到密度参数（零件未设置材料），该单元格不可编辑。</td>
  </tr>
  <tr>
    <td><b>密度显示 -1</b></td>
    <td>
      CATIA 报告该零件材料不统一（多材料或材料设置冲突），无法给出单一密度，该单元格不可编辑。
    </td>
  </tr>
</table>

<h4>10. 技术背景：坐标变换与惯量汇总算法</h4>
<p>本功能内部采用以下算法：</p>
<ul>
  <li>
    <b>重心坐标变换</b>（局部 → 根坐标系）：<br />
    r_根 = R × r_局部 + T<br />
    其中 R 为零件相对根产品的旋转矩阵，T 为平移向量，均由 CATIA Position.GetComponents() 读取。
  </li>
  <li>
    <b>转动惯量旋转变换</b>（局部 → 根坐标系）：<br />
    I_根 = R × I_局部 × R^T
  </li>
  <li>
    <b>装配级汇总（平行轴定理）</b>：对于一个装配节点，将各子零件的重心处惯量
    先移至根坐标原点再汇总，最后用平行轴定理移回总重心：<br />
    I_总重心 = Σ(I_i) + Σ m_i (|r_i|²E - r_i ⊗ r_i) − M (|r_c|²E - r_c ⊗ r_c)
  </li>
  <li>
    <b>零件级多惯量包络体汇总</b>：当一个零件有多个保持测量时（"全部汇总"模式），
    同样用上述平行轴定理在零件局部坐标系下合并，得到单一等效质量特性，再执行装配级变换。
  </li>
</ul>

<hr />
<h4>三、工具</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>复制字体文件到 CATIA 目录</b></td>
    <td>
      将 ChangFangSong.ttf 字体文件复制到 CATIA 的 TrueType 字体目录。 程序会自动检测 CATIA
      安装路径，也可手动选择。
    </td>
  </tr>
  <tr>
    <td><b>复制 ISO.xml 到 CATIA 目录</b></td>
    <td>将 ISO.xml 标准文件复制到 CATIA 的 drafting 标准目录， 用于设置制图标准。</td>
  </tr>
  <tr>
    <td><b>刷写零件模板</b></td>
    <td>
      为选中的 CATPart 文件批量添加标准用户自定义属性
      （物料编码、物料名称、规格型号、物料来源、数据状态、 存货类别、重量、备注）。
    </td>
  </tr>
  <tr>
    <td><b>宏</b></td>
    <td>
      自动扫描 macros 文件夹中的 .catvbs / .catscript / .catvba 文件，
      可直接在菜单中运行。支持打开宏文件夹和刷新宏列表。
    </td>
  </tr>
  <tr>
    <td><b>紧固件快速装配</b></td>
    <td>
      使用 VBA 宏快速批量装配紧固件到产品孔位。<br />
      支持自动对齐孔轴线、定位紧固件中心，以及装配后即时翻转方向。<br />
      <i>需要在 CATIA 中打开紧固件 CATPart 文件和目标 CATProduct 文件。</i>
    </td>
  </tr>
  <tr>
    <td><b>托板螺母快速装配</b></td>
    <td>
      使用 VBA 宏快速批量装配托板螺母到产品孔位。<br />
      通过选择托板螺母两个铆钉孔确定参考几何，再依次选择安装孔完成批量装配，支持即时翻转方向。<br />
      <i>需要在 CATIA 中打开托板螺母 CATPart 文件和目标 CATProduct 文件。</i>
    </td>
  </tr>
</table>

<h4>四、视图</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>显示 Log</b></td>
    <td>打开日志窗口，查看操作记录和错误信息，方便排查问题。</td>
  </tr>
</table>

<h4>五、 CATIA 连接状态指示器（状态栏）</h4>
<p>主窗口状态栏右侧显示 CATIA COM 连接状态，每 5 秒自动刷新：</p>
<table border="0" cellpadding="4">
  <tr>
    <td><span style="color: #2a9d2a">● CATIA 已连接</span></td>
    <td>COM 对象成功获取，功能性测试通过——连接完全正常。</td>
  </tr>
  <tr>
    <td><span style="color: #c97a00">⚠ CATIA 连接异常</span></td>
    <td>
      COM 对象可获取（CATIA 在运行），但访问属性时失败。<br />
      通常原因： CATIA 正在初始化，或 ProgID/CLSID 注册异常。稍候重试即可。
    </td>
  </tr>
  <tr>
    <td><span style="color: #cc2222">● CATIA 未连接</span></td>
    <td>
      CATIA V5 未运行或 COM 完全不可用。请先打开 CATIA V5 R28。<br />
      <b>注意：</b>若同时安装了 3DEXPERIENCE，程序会自动通过枚举 ROT 查找 CATIA V5，
      无需手动干预。如仍提示未连接，请手动启动 CATIA V5 R28 后再操作。
    </td>
  </tr>
</table>
<p>
  通过菜单 <b>帮助 → CATIA 连接诊断</b> 可查看详细诊断报告，包含 CATIA 版本、
  已打开文档数、活动文档名称及建议操作。
</p>

<hr />
<h3>常见问题</h3>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Q: 提示无法连接 CATIA ？</b></td>
    <td>
      A: 请确认 CATIA V5 已启动并处于运行状态。程序通过 COM 自动化接口 与 CATIA 通信，需要先打开
      CATIA 。
    </td>
  </tr>
  <tr>
    <td><b>Q: 状态栏显示橙色"⚠ CATIA 连接异常"是什么意思？</b></td>
    <td>
      A: 这表示 CATIA 进程确实在运行， COM 对象也能获取，但对它的功能性调用失败了。<br />
      通常是 CATIA 尚未完全启动或 COM 注册暂时异常，稍候重试即可。<br />
      可通过菜单 <b>帮助 → CATIA 连接诊断</b> 查看详细原因。
    </td>
  </tr>
  <tr>
    <td><b>Q: 同时安装了 CATIA V5 和 3DEXPERIENCE，程序显示"CATIA 未连接"或执行命令时打开了 3DEXPERIENCE？</b></td>
    <td>
      A: 这是 Windows COM 注册表被 3DEXPERIENCE 覆盖导致的冲突问题。<br />
      <b>原因：</b>两套产品均使用 "CATIA.Application" COM ProgID；后安装的版本会覆盖注册表中该 ProgID
      的 CLSID 映射，导致 COM 调用找不到 V5 实例，转而启动 3DEXPERIENCE。<br />
      <b>本程序已内置自动解决方案：</b>
      <ol>
        <li>优先通过枚举 Windows ROT（Running Object Table）直接查找 CATIA V5 实例，绕过 ProgID→CLSID 注册表映射。</li>
        <li>若 CATIA V5 未运行，程序会自动从注册表检测 CATIA V5 安装路径（优先选择 V5 而非 3DE），
            并启动 CNEXT.exe。</li>
      </ol>
      <b>若仍出现问题：</b>请确保先手动启动 CATIA V5 R28，再使用本程序。
    </td>
  </tr>
  <tr>
    <td><b>Q: 复制文件提示权限不足？</b></td>
    <td>
      A: CATIA 通常安装在 Program Files 目录，需要管理员权限才能写入。
      请右键以管理员身份运行本程序。
    </td>
  </tr>
  <tr>
    <td><b>Q: BOM 导出的 Excel 打开后乱码？</b></td>
    <td>A: 导出使用 UTF-8 编码，请确保使用较新版本的 Excel 打开。</td>
  </tr>
  <tr>
    <td><b>Q: 如何添加自定义宏？</b></td>
    <td>
      A: 点击菜单"宏 → 打开宏文件夹"，将 .catvbs 或 .catscript 或
      .catvba（CATMain函数须位于'模块1'中） 文件放入该文件夹，然后点击"刷新宏列表"即可。
    </td>
  </tr>
  <tr>
    <td><b>Q: 质量特性统计为何某些零件显示 "—"（破折号）？</b></td>
    <td>
      A: 视当前数据来源模式不同，原因有所区别：<br /><br />
      <b>Analyze 模式下：</b><br />
      ① 零件未在 CATIA 中赋材料（material），导致 CATIA 返回质量为 0；<br />
      ② 零件为曲面体（无封闭体积），CATIA 无法计算质量；<br />
      ③ Analyze API 调用失败（部分旧版 pycatia 或特殊零件类型不支持）。<br />
      解决方法：在 CATIA 中为零件赋材料（或直接设置密度），更新后右键选择"重新读取质量特性"重试。<br /><br />
      <b>惯量包络体模式下：</b><br />
      ① 零件未在 CATIA 中执行过"形状 → 测量惯量 → 保持测量"；<br />
      ② 测量是在产品窗口下建立的（而非单独打开零件文件），导致参数命名前缀与预期不符；<br />
      ③ 惯量包络体编号超过读取上限（默认最多读取编号 1–20）。<br />
      解决方法：<b>单独打开</b>该零件文件，执行测量后重新加载，或右键点击该行选择"重新读取质量特性"。
    </td>
  </tr>
</table>

<hr />
<p style="color: #888">
  开发者：{APP_AUTHOR} | 联系方式：{APP_CONTACT}<br />
  仅供内部使用，请勿外传。
</p>
"""


# 英文帮助文档全量译版：与 _HELP_HTML 保持相同标题层级/表格/图片路径结构；
# CATIA 真实属性名（惯量包络体.N、质量、密度、Gx、IoxG...）、代码与文件名一律不翻译。
_HELP_HTML_EN = f"""\
<h2>{APP_NAME} v{APP_VERSION} - Help Documentation</h2>

<h3>Overview</h3>
<p>
  {APP_NAME} is a CATIA V5 assistant tool for engineering teams, designed to streamline daily
  operations and boost productivity. It supports batch export of drawings and parts, BOM
  management, mass-properties statistics, quick launch of macro scripts, and one-click
  deployment of CATIA resource files.
</p>

<hr />
<h3>Requirements</h3>
<ul>
  <li>Operating system: Windows 10 / 11</li>
  <li>CATIA V5 R28 installed (file export and other features require CATIA to be running)</li>
</ul>

<hr />
<h3>Features</h3>

<h4>1. Export</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>CATDrawing → PDF</b></td>
    <td>
      Batch-export CATDrawing files to PDF.<br />
      Supports a custom file prefix for the output files (default: DR_).<br />
      <i>Note: for multi-page drawings, enable "Save multi-page documents in the single vector
      file" in CATIA (Tools → Options → General → Compatibility → Graphic formats → Export).</i>
    </td>
  </tr>
  <tr>
    <td><b>CATPart / CATProduct → STP</b></td>
    <td>
      Batch-export CATPart or CATProduct files to STEP format.<br />
      Supports a custom file prefix for the output files (default: MD_).
    </td>
  </tr>
  <tr>
    <td><b>Export BOM from CATProduct</b></td>
    <td>
      Extract the complete BOM information from the currently open CATProduct and export it to
      Excel (.xlsx).<br />
      You can choose the columns to include, add custom columns, and select the export hierarchy.
    </td>
  </tr>
</table>

<h4>2. Editing</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>BOM attribute completion</b></td>
    <td>
      Load the BOM attributes of the current CATProduct into a table, and edit fields such as
      part number, nomenclature, definition, version, source, as well as custom user attributes
      (<code>物料编码</code> material code, <code>物料名称</code> material name,
      <code>规格型号</code> specification, etc.) directly in the table. After editing, write all
      changes back to CATIA with one click.<br />
      <i>Attribute changes for the same file are synchronized automatically.</i><br /><br />
      <b>Main features:</b>
      <ul>
        <li><b>Dirty-field highlighting</b>: modified cells are shown in orange bold text;
        hover to see the original value before the change.</li>
        <li><b>Undo / redo</b> (Ctrl+Z / Ctrl+Y): up to 10 steps of undo and redo.</li>
        <li><b>Search filter</b> (Ctrl+F): type keywords in the search box to filter table rows
        in real time.</li>
        <li><b>Column-header sorting</b>: click any column header to sort ascending / descending.</li>
        <li><b>Save and write back</b> (Ctrl+S): write changes back to CATIA with one click.
        Closing with unsaved changes shows a confirmation prompt.</li>
        <li><b>Context menu</b>: copy cell content, open the file's containing folder, etc.</li>
        <li><b>Export Excel</b>: after export, a popup offers "Open file" and "Open containing
        folder" shortcut buttons.</li>
        <li><b>Window position memory</b>: the dialog's size and position are saved automatically
        on close and restored on the next start.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>Mass, center of gravity, and inertia statistics</b></td>
    <td>
      Traverse the product tree of the current CATProduct, read the mass (weight), center of
      gravity, and moment of inertia of every part, aggregate them automatically by assembly
      hierarchy in the root-product coordinate system, and support export to Excel.<br />
      Two data sources are supported:
      <ul>
        <li><b>Analyze mode</b> (default): computes in real time through the CATIA Analyze API;
        it works as soon as a material is assigned to the part, with no extra preparation.</li>
        <li><b>Inertia envelope mode</b>: reads the parameters written by the SPA "Measure
        Inertia + Keep Measure" commands; a keep measure must be performed for every part in
        advance. See the detailed section below.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>New drawing</b></td>
    <td>
      Generate a new drawing in CATIA for the currently active CATPart or CATProduct based on
      the CATDrawing templates in the drawing_templates folder.<br />
      <i>The target part/product must be open in CATIA, and the *.CATDrawing templates must be
      placed in the drawing_templates folder.</i>
    </td>
  </tr>
  <tr>
    <td><b>Refresh drawing</b></td>
    <td>
      Refresh the parameters of the currently active CATDrawing in CATIA (part number,
      nomenclature, version, and custom attributes) to match its associated part/product.<br />
      <i>Both the target drawing and its associated part/product must be open in CATIA.</i>
    </td>
  </tr>
</table>

<hr />
<h3>Mass, Center of Gravity, and Inertia Statistics - Detailed Notes</h3>

<h4>1. Overview</h4>
<p>
  This feature reads the mass (kg), center-of-gravity coordinates (mm), and moment-of-inertia
  tensor (kg·m²) of every part from the product tree, transforms all data into the
  <b>root-product coordinate system</b>, aggregates it level by level along the assembly
  hierarchy, and finally displays the complete mass properties of the whole product tree, with
  export to Excel supported.
</p>
<p>
  Two data sources are supported, switched with the <b>"Data source"</b> radio buttons at the
  top of the dialog:
</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Analyze</b> (default)</td>
    <td>
      Requests mass properties from the CATIA calculation engine in real time through the pycatia
      Analyze API (<code>product.analyze</code>) - no keep measure needs to be created manually
      in CATIA.<br />
      <b>Prerequisite:</b> a material must be assigned to the part (CATIA material library or a
      manually set density) so that CATIA returns a non-zero mass.<br />
      When this mode is selected, the "Inertia envelope reading" options are automatically
      disabled (grayed out).
    </td>
  </tr>
  <tr>
    <td><b>Inertia envelope</b></td>
    <td>
      Reads the keep-measure parameters written into the parameter tree by the CATIA SPA toolbar
      commands "Measure Inertia + Keep Measure", i.e.
      <code>惯量包络体.1</code>…<code>惯量包络体.N</code>.<br />
      <b>Prerequisite:</b> the user must perform the keep-measure operation for every part in
      advance (see Section 2).<br />
      Supports multiple envelope read modes (read .1 only / highest number / aggregate all),
      suitable for multi-material parts.
    </td>
  </tr>
</table>
<p>
  All internal calculations use the <b>International System of Units (SI)</b>: mass in kg,
  coordinates in mm, moment of inertia in kg·mm². Display and export convert to convenient
  units according to the selected unit (g/kg, mm/m, g·mm²/g·m²/kg·mm²/kg·m²).
</p>

<h4>2. Data Source: Analyze Mode in Detail</h4>
<p>
  Analyze mode calls the CATIA calculation engine directly and does not depend on any manual
  preparation. The exact mechanism is as follows:
</p>
<ul>
  <li>
    The program obtains the root Product of the part document
    (<code>ReferenceProduct.Parent.Product</code>) and calls:
    <ul>
      <li><code>analyze.mass</code> → part mass (kg)</li>
      <li><code>analyze.get_gravity_center()</code> → center of gravity in the part-local
      coordinate system (mm)</li>
      <li><code>analyze.get_inertia()</code> → moment-of-inertia tensor about the center of
      gravity (kg·mm², 9 elements, row-major)</li>
      <li><code>analyze.volume</code> → volume (mm³), used to derive density (kg/m³)</li>
    </ul>
  </li>
  <li>
    <b>Coordinate-system semantics</b> are identical to the inertia-envelope mode: the returned
    values are in the <b>part-local coordinate system</b>, and the program transforms them into
    the root-product coordinate system before aggregation; assembly placement is fully
    determined by the CATIA Position matrix.
  </li>
  <li>
    <b>If a part has no material</b>, CATIA returns <code>mass == 0</code>, and the program
    marks the part as "—" (no mass properties read), the same display as an envelope-mode part
    with no keep measure found.
  </li>
  <li>
    <b>Density</b> is computed as <code>mass / volume</code>; if volume reading fails, density
    is displayed as "—" (not editable).
  </li>
</ul>
<p><b>Analyze mode does not support</b> merging multiple inertia envelopes (multiple material
zones) — each part only gets a single overall mass property. To measure several material zones
separately and then aggregate them, use the "Aggregate all" read mode of the inertia-envelope
source.</p>

<h4>3. Data Source: Inertia-Envelope Prerequisite (Keep Measure)</h4>
<p>
  <b>Only when using the "Inertia envelope" data source</b> do you need to complete the following
  operations for every part in the product tree:
</p>
<ol>
  <li>
    <b>Open the part file by itself</b> (CATPart); do not operate inside the product window.<br />
    <i>Reason: measures created in the product window use the root-product coordinate system as
    reference instead of the part's own coordinate system. This feature expects the center of
    gravity in the part-local coordinate system, which the program then transforms to the
    root-product coordinate system. If a measure already uses the root-product system, results
    for parts whose local systems do not coincide will be wrong.</i>
  </li>
  <li>Run the menu <b>Measure → Measure Inertia</b>.</li>
  <li>In the dialog, check <b>"Keep Measure"</b> and confirm.</li>
  <li>
    CATIA will create a geometry named <code>惯量包络体.1</code> in the parameter tree,
    containing the following parameters:<br />
    <code>质量</code> (kg), <code>密度</code> (kg/m³), <code>Gx / Gy / Gz</code>
    (center-of-gravity coordinates, mm), <code>IoxG / IoyG / IozG / IxyG / IxzG / IyzG</code>
    (inertia components, kg·m²)
  </li>
  <li>
    To record several measurements for the same part (for example a body with different
    materials), repeat the steps above; CATIA will generate
    <code>惯量包络体.2</code>, <code>惯量包络体.3</code>… in turn. This feature reads kept
    measures numbered 1 to {MAX_INERTIA_INDEX} at most.
  </li>
</ol>
<p>
  <b>Important: minimum parameters to check in the custom measurement</b><br />
  The "Measure Inertia" dialog supports choosing which parameters to save via the "Custom…"
  button. This feature needs to read the following parameters, so <b>please make sure all of
  them are checked</b>; otherwise the inertia envelope will lack the required data and reading
  will fail:
</p>
<ul>
  <li>Density / surface mass</li>
  <li>Mass</li>
  <li>Center of gravity (G)</li>
  <li>Inertia matrix at center of gravity</li>
</ul>
<p>The image below shows the minimum required selection in the "Custom measure" dialog
(checking fewer items than this prevents normal reading):</p>
<p>
  <img src="inertia_keep_params.png" alt="Minimum parameters for keeping inertia measure" style="max-width: 480px" />
</p>
<p><b>Tip:</b> inertia envelopes created by Measure Inertia must be updated manually; a global
update does not update them.</p>

<h4>4. How to Open and Data Sources</h4>
<p>Click the menu <b>Edit → Mass, Weight, and Inertia Statistics</b> to open the statistics
dialog. There are two ways to load data:</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Load live from CATIA</b></td>
    <td>
      <ul>
        <li>
          Check <b>"Use the current active CATIA document"</b>: directly reads the CATProduct
          currently active in CATIA.
        </li>
        <li>
          Or click <b>"Browse…"</b> to pick a CATProduct file on disk and then click
          <b>"Load"</b>. CATIA opens the file automatically (if not already open) before the
          traversal.
        </li>
      </ul>
      A progress message is shown while loading; large products may take a few minutes.
    </td>
  </tr>
  <tr>
    <td><b>Load from saved data</b></td>
    <td>
      Click <b>"Load saved data…"</b> and choose a <code>.mpd</code> data file previously saved
      with the "Save data" button. This requires no running CATIA and is suitable for offline
      analysis or data sharing.
    </td>
  </tr>
  <tr>
    <td><b>Append data… / Append active document…</b></td>
    <td>
      After a base product has been loaded, you can append and merge additional sub-assembly mass
      properties; this suits cases where the main product is too large and each sub-assembly
      needs to be collected separately.
      <ul>
        <li>
          <b>Append data…</b>: appends sub-assembly mass properties from one or more
          <code>.mpd</code> data files (multiple files can be selected at once).
        </li>
        <li>
          <b>Append active document…</b>: appends the mass properties of the currently active
          CATIA document (a sub-assembly CATProduct) to the existing data in real time.
        </li>
      </ul>
      <b>Prerequisite: the coordinate systems of all sub-assemblies must match the main product
      (and each other); the program performs no additional coordinate transformation.</b><br />
      The two buttons are enabled only after the base data is loaded; if there is no data yet,
      the operation is blocked with a message.
    </td>
  </tr>
</table>

<h4>5. Reading and Display Options</h4>

<p><b>5.1 Data source</b> (radio buttons at the top of the dialog)</p>
<p>See Section 1 for the overview; switch between Analyze mode and Inertia envelope mode.
When Analyze is selected, the "Inertia envelope reading" options below are disabled
automatically.</p>

<p><b>5.2 Inertia envelope read mode</b> (only effective for the "Inertia envelope" source;
affects loading performance)</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Read .1 only</b></td>
    <td>
      Reads only <code>惯量包络体.1</code> of each part. Fastest: each part requires a single
      parameter query. Suitable for the usual case where each part has one keep measure.
    </td>
  </tr>
  <tr>
    <td><b>Highest number</b></td>
    <td>
      Scans envelopes numbered 1 to 20 and takes the valid keep measure with the highest number.
      Suitable when "the latest measurement" should win (for example re-measuring and keeping the
      last one).<br />
      Slower: every missing number triggers one COM exception, up to 19 times.
    </td>
  </tr>
  <tr>
    <td><b>Aggregate all</b> (default)</td>
    <td>
      Reads all valid numbers (1–20) and aggregates them into a single mass property at part
      level using the <b>parallel-axis theorem</b>.<br />
      Suitable when a part has several separately measured zones (for example each zone of a
      multi-material part has its own inertia envelope).<br />
      Same speed as "Highest number" mode.
    </td>
  </tr>
</table>

<p><b>5.3 BOM display mode</b></p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Hierarchical BOM</b> (default)</td>
    <td>
      Shows the complete product tree in a tree structure, including parts (leaf nodes) and
      products/components (intermediate nodes).<br />
      Product/component rows show the <b>aggregated values</b> of all parts in their subtree (in
      the root-product coordinate system). Part rows show Weight / CogX/Y/Z / Ixx–Iyz already
      transformed into the <b>root-product coordinate system</b>, which depends on the assembly
      placement.
    </td>
  </tr>
  <tr>
    <td><b>Summary BOM</b></td>
    <td>
      Merges identical parts by part number; each unique part number (Part Number) is shown as
      one row with the quantity (Quantity). Lists parts only (no product or component nodes).
      Weight / CogX/Y/Z / Ixx–Iyz are shown in the <b>part's own coordinate system</b>,
      independent of assembly placement.<br />
      Sorting columns can be selected (part number, nomenclature, revision, file name, quantity,
      weight, etc.).
    </td>
  </tr>
</table>

<p><b>5.4 Unit settings</b></p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Weight unit</b></td>
    <td>g or kg (default g).</td>
  </tr>
  <tr>
    <td><b>Length unit (center of gravity)</b></td>
    <td>mm or m (default mm).</td>
  </tr>
  <tr>
    <td><b>Inertia unit</b></td>
    <td>g·mm², g·m², kg·mm² or kg·m² (default g·mm²). The inertia unit is selected
    independently of the weight and length units.</td>
  </tr>
</table>
<p>Switching units updates the table in real time; column headers automatically gain the current
unit suffix, and all settings persist across sessions.</p>

<p><b>5.5 Optional display columns</b></p>
<p>Use the checkboxes in the "Display columns" area to show or hide the following columns:</p>
<ul>
  <li><b>Filename</b>: the disk file name of the part.</li>
  <li><b>Part Number</b>: the PartNumber property in CATIA.</li>
  <li><b>Nomenclature</b>: the Nomenclature property in CATIA.</li>
  <li><b>Revision</b>: the Revision property in CATIA.</li>
</ul>

<p><b>5.6 Ignore hidden nodes</b></p>
<p>
  When checked: a part in hidden state (invisible) is skipped; a product/component in hidden
  state is skipped together with all its descendants, and none of them are included in the
  statistics.
</p>

<h4>6. Table Operations</h4>

<p>
  The table supports <b>multi-selection</b> (Ctrl+click or Shift+click). With multiple rows
  selected, the right-click context menu applies batch operations to all selected rows (batch
  delete, batch toggle "participate in calculation", batch re-read). If the right-clicked row is
  not in the current selection set, the program clears the previous selection and selects only
  that row (degrading to single selection).
</p>

<p><b>6.1 Editing weight</b></p>
<p>
  Double-click a part row's "Weight" cell to enter a value directly (only part rows are
  editable; product/component summary rows are not). After editing:
</p>
<ul>
  <li>All inertia components of that part are scaled proportionally (keeping the
  mass–inertia relationship consistent).</li>
  <li>All other rows with the same part number (Part Number) are updated synchronously.</li>
  <li>The total product mass properties are recomputed automatically and the "Summary results"
  panel at the bottom refreshes in real time.</li>
  <li>The weight change also updates the density proportionally (volume unchanged,
  density = mass / volume).</li>
</ul>
<p>The "Weight" cell does not accept 0 or negative values.</p>

<p><b>6.2 Editing density</b></p>
<p>
  Double-click a part row's "Density" cell to edit the density value directly (kg/m³). After an
  edit, that part's weight and inertia are scaled proportionally (volume unchanged,
  mass = density × volume). The density column is not editable in these cases:
</p>
<ul>
  <li>The density value is "—" (no density data in the original measure).</li>
  <li>The density value is -1 (CATIA reports non-uniform material for the part and cannot
  provide a single density).</li>
</ul>

<p><b>6.3 Right-click context menu</b></p>
<p>Right-click any row in the table to see the following menu (items marked "single selection
only" are unavailable in multi-selection):</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Open path</b></td>
    <td>(single selection only) Opens the file's containing folder in File Explorer.</td>
  </tr>
  <tr>
    <td><b>Copy path</b></td>
    <td>(single selection only) Copies the full path of the file for that row to the clipboard.</td>
  </tr>
  <tr>
    <td><b>Open in CATIA</b></td>
    <td>(single selection only) Opens the part/product file in CATIA through the system
    associated program.</td>
  </tr>
  <tr>
    <td><b>Re-read mass properties</b></td>
    <td>
      (part rows only) Re-reads the kept inertia-envelope measure parameters of that part from
      CATIA without re-traversing the product tree, and synchronously updates all nodes with the
      same part number plus the summary results. Suitable for quickly refreshing a single part
      after its measure was updated in CATIA. In multi-selection it is applied in batch to all
      selected part rows.
    </td>
  </tr>
  <tr>
    <td><b>Add mirror part</b></td>
    <td>
      (Hierarchical BOM mode, single selection only) Adds a virtual "mirror part" row for the
      selected row, directly above it, shown with a light-blue background.<br />
      Mirror rule: symmetric with respect to the ZX plane (CogY, Ixy, Iyz are negated; the
      remaining components stay unchanged).<br />
      Mirror rows take part in the summary calculation but their weight/density cannot be edited
      directly; when the source row is modified or deleted, the mirror row is synchronized or
      cascaded-deleted automatically. See Section 5.5.
    </td>
  </tr>
  <tr>
    <td><b>Participate in calculation: √ / ×</b></td>
    <td>
      (Hierarchical BOM mode) Toggles whether the row takes part in the mass summary
      calculation. Excluded rows are shown in gray italic; all instances sharing the same part
      number are excluded/restored together. Excluded rows are not counted in the bottom summary
      and do not appear in the Summary BOM.<br />
      In multi-selection: if all selected rows are already excluded, all of them are restored;
      otherwise all are marked as excluded.
    </td>
  </tr>
  <tr>
    <td><b>Delete from list</b></td>
    <td>
      (Hierarchical BOM mode) Removes the row and all its descendant rows from the display list
      (does not affect the CATIA files). A confirmation dialog is shown before deletion to
      prevent mistakes. If the row has an associated mirror row, the mirror row is cascade-deleted
      as well.<br />
      In multi-selection it is applied in batch to all selected rows and their subtrees.
    </td>
  </tr>
</table>

<p><b>6.4 Expand / collapse / auto-fit columns</b></p>
<p>
  The bottom button bar provides "Expand all", "Collapse all", and "Auto-fit columns" buttons
  for browsing large assembly trees. Column widths can be resized manually and are preserved
  after switching modes. After re-loading or appending data, the expand/collapse state from
  before the load is restored automatically.
</p>

<p><b>6.5 Mirror part (virtual rows)</b></p>
<p>
  In Hierarchical BOM mode, right-click any row (part, product, or component) and choose
  <b>"Add mirror part"</b> to insert a <b>virtual mirror row</b> directly above it. Mirror
  rows are shown with a <b>light-blue background</b> and their type column reads "mirror part".
</p>
<p>Mirror rules (mirrored with respect to the ZX plane, i.e. the Y axis is negated):</p>
<ul>
  <li>Center of gravity: CogX, CogZ unchanged, CogY → −CogY (root coordinate system)</li>
  <li>Moment of inertia: Ixx, Iyy, Izz, Ixz unchanged, Ixy → −Ixy, Iyz → −Iyz (root
  coordinate system)</li>
  <li>Mass (weight) unchanged</li>
</ul>
<p>Usage notes:</p>
<ul>
  <li>Mirror rows <b>take part in the summary calculation</b>; their mass-property contribution
  is treated the same as the source row.</li>
  <li>Mirror rows <b>cannot be edited directly</b> (weight or density); edit the source row
  instead and the mirror row updates synchronously.</li>
  <li><b>Cascade delete</b>: deleting the source row also deletes the associated mirror row.</li>
  <li>Mirror rows support the "participate in calculation √/×" toggle as well.</li>
  <li>Mirror rows appear in the exported Excel; their Status column is marked as mirror.</li>
</ul>

<h4>7. Summary Results Panel</h4>
<p>
  The "Summary results (based on the root-product coordinate system)" panel below the table
  shows the aggregated results, in the root-product coordinate system, of all active (that is,
  <b>not excluded</b>) valid parts in the current product tree. The panel has three columns:
</p>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Left: total weight + center of gravity (G)</b></td>
    <td>
      <ul>
        <li><b>Total weight</b>: shown in the currently selected weight unit (g or kg).</li>
        <li><b>Center of gravity Gx / Gy / Gz</b>: shown in the currently selected length unit
        (mm or m).</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td><b>Middle: inertia matrix</b></td>
    <td>
      Shows the moment-of-inertia tensor about the center of gravity as a full 3×3 matrix:<br />
      Ixx, Ixy, Ixz<br />
      Iyx, Iyy, Iyz<br />
      Izx, Izy, Izz<br />
      (shown in the currently selected inertia unit; the matrix is symmetric, Ixy=Iyx, etc.)
    </td>
  </tr>
  <tr>
    <td><b>Right: principal moments + principal axes</b></td>
    <td>
      Performs an eigendecomposition of the inertia matrix and gives:
      <ul>
        <li><b>Principal moments M1 / M2 / M3</b>: the three principal moments (in ascending
        order, shown in the current inertia unit).</li>
        <li>
          <b>Principal axes A1 / A2 / A3</b>: the unit direction vectors of the corresponding
          principal moments (each with x / y / z components, dimensionless).
        </li>
      </ul>
    </td>
  </tr>
</table>
<p>The panel refreshes automatically whenever weight/density is edited, the excluded state is
toggled, or data is appended.</p>

<h4>8. Export and Save</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Export table</b></td>
    <td>
      Exports the current table (including the bottom summary rows) to an Excel (.xlsx) file.
      Export content: all currently visible columns (without the internal index column
      "#"), with a Status column appended at the end.<br />
      Excluded rows are marked with a special background color; rows whose measurements failed
      are marked in orange; all values are exported in the currently selected units.
    </td>
  </tr>
  <tr>
    <td><b>Save data…</b></td>
    <td>
      Saves the current row data as a <code>.mpd</code> compressed binary file (gzip-compressed
      JSON). The saved data can be reopened at any time via "Load saved data…" without
      connecting to CATIA.
    </td>
  </tr>
</table>

<h4>9. Common Error States</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>— (dash)</b></td>
    <td>
      Mass-property reading failed. The cause depends on the data source mode:<br />
      <b>Analyze mode:</b> the part has no material or is a surface body, so CATIA returns a
      mass of 0. Assign a material to the part in CATIA, then right-click and choose
      "Re-read mass properties".<br />
      <b>Inertia envelope mode:</b> the part has no "Keep Measure", or no
      <code>惯量包络体.x</code> was found in the CATIA parameter tree. <b>Open</b> the part
      <b>by itself</b>, run the measure, and reload.
    </td>
  </tr>
  <tr>
    <td><b>File not found</b></td>
    <td>The part file cannot be found on disk; it may have been moved or renamed. Mass
    properties cannot be read.</td>
  </tr>
  <tr>
    <td><b>Density shows —</b></td>
    <td>No density parameter was read during the CATIA measure (the part has no material); the
    cell is not editable.</td>
  </tr>
  <tr>
    <td><b>Density shows -1</b></td>
    <td>
      CATIA reports non-uniform materials for the part (multiple materials or conflicting material
      settings) and cannot give a single density; the cell is not editable.
    </td>
  </tr>
</table>

<h4>10. Technical Background: Coordinate Transformation and Inertia Aggregation</h4>
<p>This feature uses the following algorithms internally:</p>
<ul>
  <li>
    <b>Center-of-gravity transformation</b> (local → root):<br />
    r_root = R × r_local + T<br />
    where R is the rotation matrix of the part relative to the root product and T is the
    translation vector, both read via CATIA Position.GetComponents().
  </li>
  <li>
    <b>Moment-of-inertia rotation</b> (local → root):<br />
    I_root = R × I_local × R^T
  </li>
  <li>
    <b>Assembly-level aggregation (parallel-axis theorem)</b>: for one assembly node, move each
    child part's inertia about its own center of gravity to the root origin, sum them, then use
    the parallel-axis theorem to move back to the total center of gravity:<br />
    I_total = Σ(I_i) + Σ m_i (|r_i|²E - r_i ⊗ r_i) − M (|r_c|²E - r_c ⊗ r_c)
  </li>
  <li>
    <b>Part-level multi-envelope aggregation</b>: when a part has several keep measures ("Aggregate
    all" mode), the same parallel-axis theorem is applied to merge them in the part-local
    coordinate system into one equivalent mass property, which then undergoes the assembly-level
    transformation.
  </li>
</ul>

<hr />
<h4>3. Tools</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Copy font file to CATIA directory</b></td>
    <td>
      Copies the ChangFangSong.ttf font file to the CATIA TrueType font directory. The program
      auto-detects the CATIA installation path, or you can choose it manually.
    </td>
  </tr>
  <tr>
    <td><b>Copy ISO.xml to CATIA directory</b></td>
    <td>Copies the ISO.xml standard file to the CATIA drafting standards directory, used to set
    the drafting standard.</td>
  </tr>
  <tr>
    <td><b>Stamp part template</b></td>
    <td>
      Batch-adds standard user-defined attributes to the selected CATPart files
      (<code>物料编码</code> material code, <code>物料名称</code> material name,
      <code>规格型号</code> specification, <code>物料来源</code> material source,
      <code>数据状态</code> data status, <code>存货类别</code> inventory category,
      <code>重量</code> weight, <code>备注</code> remarks).
    </td>
  </tr>
  <tr>
    <td><b>Run Macro</b></td>
    <td>
      Automatically scans .catvbs / .catscript / .catvba files in the macros folder and runs them
      directly from the menu. Also supports opening the macro folder and refreshing the macro
      list.
    </td>
  </tr>
  <tr>
    <td><b>Quick assembly of fasteners</b></td>
    <td>
      Uses a VBA macro to quickly batch-assemble fasteners into product holes.<br />
      Supports automatic alignment to the hole axis, positioning of the fastener center, and
      flipping the direction immediately after assembly.<br />
      <i>The fastener CATPart file and the target CATProduct file must be open in CATIA.</i>
    </td>
  </tr>
  <tr>
    <td><b>Quick assembly of plate nuts</b></td>
    <td>
      Uses a VBA macro to quickly batch-assemble plate nuts into product holes.<br />
      Selects the reference geometry through the plate nut's two rivet holes, then selects the
      mounting holes one by one to complete the batch assembly; the direction can be flipped
      immediately.<br />
      <i>The plate-nut CATPart file and the target CATProduct file must be open in CATIA.</i>
    </td>
  </tr>
</table>

<h4>4. View</h4>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Show Log</b></td>
    <td>Opens the log window to view operation records and error messages for troubleshooting.</td>
  </tr>
</table>

<h4>5. CATIA Connection Indicator (Status Bar)</h4>
<p>The right side of the main window's status bar shows the CATIA COM connection status,
refreshed every 5 seconds:</p>
<table border="0" cellpadding="4">
  <tr>
    <td><span style="color: #2a9d2a">● CATIA Connected</span></td>
    <td>The COM object was acquired successfully and functional tests pass — the connection is
    fully healthy.</td>
  </tr>
  <tr>
    <td><span style="color: #c97a00">⚠ CATIA Connection Problem</span></td>
    <td>
      The COM object can be acquired (CATIA is running) but accessing its properties fails.<br />
      Usually because CATIA is still initializing, or the ProgID/CLSID registration is abnormal.
      Retry after a moment.
    </td>
  </tr>
  <tr>
    <td><span style="color: #cc2222">● CATIA Not Connected</span></td>
    <td>
      CATIA V5 is not running or COM is completely unavailable. Please start CATIA V5 R28
      first.<br />
      <b>Note:</b> if 3DEXPERIENCE is also installed, the program automatically enumerates the
      ROT to find the CATIA V5 instance, so no manual intervention is needed. If it still says
      "not connected", start CATIA V5 R28 manually and retry.
    </td>
  </tr>
</table>
<p>
  Use the menu <b>Help → CATIA Connection Diagnostics</b> to view a detailed diagnostic report,
  including the CATIA version, the number of open documents, the active document name, and
  recommended actions.
</p>

<hr />
<h3>FAQ</h3>
<table border="0" cellpadding="4">
  <tr>
    <td><b>Q: "Unable to connect to CATIA"?</b></td>
    <td>
      A: Make sure CATIA V5 is started and running. The program communicates with CATIA through
      the COM automation interface, so CATIA must be opened first.
    </td>
  </tr>
  <tr>
    <td><b>Q: What does the orange "⚠ CATIA Connection Problem" in the status bar mean?</b></td>
    <td>
      A: It means the CATIA process is indeed running and the COM object can be acquired, but
      functional calls against it fail.<br />
      Usually CATIA has not finished starting up or the COM registration is temporarily
      abnormal; retry after a moment.<br />
      Use the menu <b>Help → CATIA Connection Diagnostics</b> to see the detailed cause.
    </td>
  </tr>
  <tr>
    <td><b>Q: CATIA V5 and 3DEXPERIENCE are both installed; the program shows "CATIA not
    connected" or opens 3DEXPERIENCE when running a command?</b></td>
    <td>
      A: This is a conflict caused by the Windows COM registry being overwritten by
      3DEXPERIENCE.<br />
      <b>Cause:</b> both products use the "CATIA.Application" COM ProgID; the later installation
      overwrites the CLSID mapping of that ProgID in the registry, so COM calls no longer find
      the V5 instance and instead start 3DEXPERIENCE.<br />
      <b>This program has a built-in automatic solution:</b>
      <ol>
        <li>First enumerates the Windows ROT (Running Object Table) to find the CATIA V5
        instance directly, bypassing the ProgID→CLSID registry mapping.</li>
        <li>If CATIA V5 is not running, the program automatically detects the CATIA V5
        installation path from the registry (preferring V5 over 3DE) and starts CNEXT.exe.</li>
      </ol>
      <b>If it still fails:</b> start CATIA V5 R28 manually first, then use the program.
    </td>
  </tr>
  <tr>
    <td><b>Q: "Insufficient permissions" when copying files?</b></td>
    <td>
      A: CATIA is usually installed under Program Files, so administrator rights are required to
      write there. Right-click the program and run it as administrator.
    </td>
  </tr>
  <tr>
    <td><b>Q: The exported BOM Excel shows garbled text when opened?</b></td>
    <td>A: Export uses UTF-8 encoding; make sure to open it with a recent version of Excel.</td>
  </tr>
  <tr>
    <td><b>Q: How do I add custom macros?</b></td>
    <td>
      A: Click the menu "Run Macro → Open Macro Folder", put your .catvbs or .catscript or
      .catvba files there (the CATMain function must be located in 'Module1'), then click
      "Refresh macro list".
    </td>
  </tr>
  <tr>
    <td><b>Q: Why do some parts show "—" (dash) in the mass-properties statistics?</b></td>
    <td>
      A: The cause differs by the current data source mode:<br /><br />
      <b>In Analyze mode:</b><br />
      ① The part has no material assigned in CATIA, so CATIA returns a mass of 0;<br />
      ② The part is a surface body (no closed volume) and CATIA cannot compute a mass;<br />
      ③ The Analyze API call fails (not supported by some older pycatia versions or special
      part types).<br />
      Solution: assign a material in CATIA (or set the density directly), then right-click
      "Re-read mass properties" to retry.<br /><br />
      <b>In Inertia envelope mode:</b><br />
      ① The part has never been measured via "Shape → Measure Inertia → Keep Measure" in CATIA;<br />
      ② The measure was created in the product window (not in the part file opened by itself), so
      the parameter-name prefix differs from what is expected;<br />
      ③ The envelope number exceeds the reading limit (by default numbers 1–20 are read).<br />
      Solution: <b>open</b> the part file <b>by itself</b>, run the measure, reload, or
      right-click the row and choose "Re-read mass properties".
    </td>
  </tr>
</table>

<hr />
<p style="color: #888">
  Developer: {APP_AUTHOR} | Contact: {APP_CONTACT}<br />
  For internal use only. Do not distribute.
</p>
"""


def _help_html() -> str:
    """按当前界面语言返回帮助文档 HTML。

    - en_US：返回英文全量译版 _HELP_HTML_EN；
    - 其余（含 zh_CN）一律返回源文档 _HELP_HTML（中文）。
    两份资源保持相同的图片路径与标题/表格/列表结构；CATIA 真实属性名、
    代码与文件名在英文版中同样保持原样。
    """
    if current_ui_language() == LANG_EN_US:
        return _HELP_HTML_EN
    return _HELP_HTML


class HelpDialog(QDialog):
    """Scrollable help dialog with rich-text documentation."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("CATIACopilot", "{0} — 帮助文档").format(APP_NAME))
        self.resize(700, 560)
        self.setMinimumSize(500, 500)

        self._settings = QSettings("CATIACopilot", "HelpDialog")
        
        # 恢复窗口几何
        saved_geom = self._settings.value("geometry")
        if saved_geom:
            self.restoreGeometry(saved_geom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        # Allow relative <img> paths in the HTML to resolve from the resources folder
        browser.setSearchPaths([str(resource_path("resources"))])
        browser.setHtml(_help_html())
        layout.addWidget(browser)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton(translate("CATIACopilot", "关闭"))
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def closeEvent(self, event):  # noqa: N802
        """关闭时保存窗口几何。"""
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
