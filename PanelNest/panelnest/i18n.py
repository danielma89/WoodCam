"""Global PanelNest workbench localization.

PanelNest is one FreeCAD workbench: its menus, command dialogs and the
embedded WoodCAM UI must follow the same language.  This module therefore
translates the live Qt presentation tree rooted at FreeCAD's main window.  It
does not change command names, document properties, geometry or callbacks.
"""

from __future__ import annotations

import html
import re
import sys
import weakref

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - FreeCAD/PySide2 installations
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:  # pragma: no cover - legacy FreeCAD
        from PySide import QtCore, QtGui
        QtWidgets = QtGui


PARAMETER_PATH = "User parameter:BaseApp/Preferences/PanelNest"
WOODCAM_PARAMETER_PATH = "User parameter:BaseApp/Preferences/WoodCAM2D"
LANGUAGE_KEY = "language"
ITEM_SOURCE_ROLE = int(QtCore.Qt.UserRole) + 1100


PT_EN = {
    # FreeCAD chrome visible beside the workbench menu.
    "Macro": "Macro",
    "PanelNest": "PanelNest",
    "Janelas": "Windows",
    "Ajuda": "Help",
    "Acessibilidade": "Accessibility",
    "Idioma": "Language",
    "Português": "Portuguese",
    # Main PanelNest menu and command actions.
    "Catálogo de Móveis": "Furniture Catalog",
    "Catalogo de Moveis": "Furniture Catalog",
    "Importar Peças do CSV": "Import Parts from CSV",
    "Importar Pecas do CSV": "Import Parts from CSV",
    "Etiquetar Peças": "Label Parts",
    "Etiquetar Pecas": "Label Parts",
    "Aplicar Dados": "Apply Data",
    "Aplicar Fita por Face": "Apply Edge Band by Face",
    "Aplicar Material": "Apply Material",
    "Girar Veio": "Rotate Grain",
    "Organizar para Layout": "Organize for Layout",
    "Configurar Chapa": "Configure Sheet",
    "Validar Projeto": "Validate Project",
    "Gerar Layout": "Generate Layout",
    "Layout CNC Formas": "CNC Shape Layout",
    "Exportar Arquivos": "Export Files",
    "CAM": "CAM",
    "WoodCAM 2D": "WoodCAM 2D",
    "Ferramentas Auxiliares": "Auxiliary Tools",
    "Editar Dados das Peças": "Edit Part Data",
    "Editar Dados das Pecas": "Edit Part Data",
    "Editar Ferragens": "Edit Hardware",
    "Planilha de Peças": "Parts Spreadsheet",
    "Planilha de Pecas": "Parts Spreadsheet",
    "Atualizar Estoque com Retalhos": "Update Stock with Remnants",
    "Banco de Retalhos": "Remnant Database",
    "Gerar Orçamento": "Generate Quote",
    "Gerar Orcamento": "Generate Quote",
    "Rastreio de Produção": "Production Tracking",
    "Rastreio de Producao": "Production Tracking",
    "Criar Painel de Teste": "Create Test Panel",
    "Aplicar Fita por Face": "Apply Edge Band by Face",
    "Exportar Etiquetas": "Export Labels",
    # Common dialog titles, actions and field labels.
    "Salvar": "Save",
    "Salvar como...": "Save as…",
    "Apagar": "Delete",
    "Remover": "Remove",
    "Fechar": "Close",
    "Cancelar": "Cancel",
    "Aplicar": "Apply",
    "OK": "OK",
    "Adicionar": "Add",
    "Adicionar foto": "Add photo",
    "Buscar": "Search",
    "Filtrar:": "Filter:",
    "Opções": "Options",
    "Opcoes": "Options",
    "Categorias": "Categories",
    "Material:": "Material:",
    "Espessura (mm):": "Thickness (mm):",
    "Área mínima (cm²):": "Minimum area (cm²):",
    "Area mínima (cm²):": "Minimum area (cm²):",
    "Mostrar usados": "Show used",
    "Nome": "Name",
    "Quantidade": "Quantity",
    "Material": "Material",
    "Espessura": "Thickness",
    "Comprimento": "Length",
    "Largura": "Width",
    "Altura": "Height",
    "Peças": "Parts",
    "Pecas": "Parts",
    "Fita de Borda": "Edge Band",
    "Superior": "Top",
    "Inferior": "Bottom",
    "Esquerda": "Left",
    "Direita": "Right",
    "Acão:": "Action:",
    "Ação:": "Action:",
    "Acao:": "Action:",
    "Material da fita:": "Edge-band material:",
    "Preview plano da peça": "Flat part preview",
    "Preview plano da peca": "Flat part preview",
    "Itens ignorados na seleção:": "Items ignored in the selection:",
    "Itens ignorados na selecao:": "Items ignored in the selection:",
    "Selecionar uma peça": "Select a part",
    "Selecione uma peça": "Select a part",
    "Selecione uma peca": "Select a part",
    "Adicionar Retalho": "Add Remnant",
    "Remover Retalho": "Remove Remnant",
    "Remover selecionados": "Remove selected",
    "Remover Selecionados": "Remove selected",
    "Importar Selecionados como Estoque": "Import selected as stock",
    "Purgar Usados (>180 dias)": "Purge used (>180 days)",
    "Carregar do Histórico": "Load from History",
    "Carregar do Historico": "Load from History",
    "Marcar todos": "Select all",
    "Desmarcar todos": "Clear selection",
    "Mostrar medidas nas peças": "Show dimensions on parts",
    "Mostrar medidas nas pecas": "Show dimensions on parts",
    "Mostrar etiqueta das peças": "Show part labels",
    "Mostrar etiqueta das pecas": "Show part labels",
    "Escolher Pasta e Exportar…": "Choose Folder and Export…",
    "Escolher Pasta e Exportar...": "Choose Folder and Export…",
    "Exportar somente DXF…": "Export DXF only…",
    "Exportar somente SVG…": "Export SVG only…",
    "Prévia antes de exportar:": "Preview before exporting:",
    "Previa antes de exportar:": "Preview before exporting:",
    "Abrir no navegador": "Open in browser",
    "Prévia de Etiquetas": "Label Preview",
    "Previa de Etiquetas": "Label Preview",
    "Prévia do Plano de Corte (SVG)": "Cut Plan Preview (SVG)",
    "Plano de Corte A4 (imprimir)": "A4 Cut Plan (print)",
    "Prévia do Relatório Completo": "Full Report Preview",
    "Previa do Relatorio Completo": "Full Report Preview",
    "Exportação rápida": "Quick export",
    "Exportacao rápida": "Quick export",
    "Histórico de retalhos": "Remnant history",
    "Historico de retalhos": "Remnant history",
    "Salvar retalhos no histórico": "Save remnants to history",
    "Salvar retalhos no historico": "Save remnants to history",
    "Inserir no documento →": "Insert into document →",
    "Gerar Gabinete": "Generate Cabinet",
    "Remover modelo": "Remove model",
    "Tipo de gabinete": "Cabinet type",
    "Dimensões (mm)": "Dimensions (mm)",
    "Dimensoes (mm)": "Dimensions (mm)",
    "Configurações": "Settings",
    "Configuracoes": "Settings",
    "CNC Avançado": "Advanced CNC",
    "CNC Avancado": "Advanced CNC",
    "Margem de Lucro": "Profit Margin",
    "Custos Adicionais (mão de obra, frete, etc.)": "Additional Costs (labor, freight, etc.)",
    "Custos Adicionais (mao de obra, frete, etc.)": "Additional Costs (labor, freight, etc.)",
    "Nova Ordem": "New Order",
    "+ Nova Ordem": "+ New Order",
    "Concluir Ordem": "Complete Order",
    "Alterar selecionadas para:": "Change selected to:",
    "Operador:": "Operator:",
    "Histórico de Retalhos — selecione para adicionar": "Remnant History — select to add",
    "Historico de Retalhos — selecione para adicionar": "Remnant History — select to add",
    "PanelNest: ": "PanelNest: ",
    # Command tooltips (shown in the toolbar and in the PanelNest menu).
    "Importa uma lista de peças de um arquivo CSV e cria os objetos no documento.\nColunas: nome, comprimento, largura, espessura, material, quantidade, ...": "Imports a list of parts from a CSV file and creates the objects in the document.\nColumns: name, length, width, thickness, material, quantity, ...",
    "Agrupa as pecas por material, espessura e metodo de corte para preparar o layout das chapas.": "Groups parts by material, thickness and cutting method to prepare the sheet layout.",
    "Verifica configuracao, pecas, estoque e layout para apontar pendencias antes da producao.": "Checks configuration, parts, stock and layout to identify issues before production.",
    "Acompanha o status de cada peca no fluxo de producao.\nCrie ordens, atualize status e acompanhe o progresso.": "Tracks each part's status through production.\nCreate orders, update statuses and monitor progress.",
    "Resolve faces laterais selecionadas para os lados da peca e marca, remove ou alterna a fita de borda.": "Resolves selected side faces to the part sides and marks, removes or toggles edge banding.",
    "Aplica cor de material às peças selecionadas.\nCompatível com a visualização de fita de borda.": "Applies a material color to selected parts.\nCompatible with edge-band visualization.",
    "Gira o sentido do veio/fibra 90° nas peças selecionadas.\nAfeta o layout de corte: peças com veio girado são posicionadas rotacionadas na chapa.": "Rotates the grain direction 90° on selected parts.\nAffects the cut layout: parts with rotated grain are placed rotated on the sheet.",
    "Cria ou atualiza uma planilha de pecas a partir da selecao atual ou de todas as pecas visiveis.": "Creates or updates a parts spreadsheet from the current selection or all visible parts.",
    "Define chapa, estoque, espacamentos, estrategias de corte e exibicao do layout para o documento atual.": "Defines sheet, stock, spacing, cutting strategies and layout display for the current document.",
    "Cria um painel de MDF de exemplo para validar a bancada.": "Creates a sample MDF panel to validate the workbench.",
    "Associa dobraças, corrediças, parafusos e outros acessórios a cada peça do projeto.": "Associates hinges, slides, screws and other hardware with each project part.",
    "Abre uma tabela para revisar e editar material, corte, rotacao, veio/fibra e fita de borda por peca.": "Opens a table to review and edit material, cutting, rotation, grain and edge banding per part.",
    "Exporta as planilhas do PanelNest em CSV, gera relatorio e etiquetas em PDF, salva DXF e publica o guia no Cloudflare Pages quando configurado.": "Exports PanelNest spreadsheets as CSV, generates reports and PDF labels, saves DXF and publishes the guide to Cloudflare Pages when configured.",
    "Catálogo pessoal de móveis.\nImporte projetos FreeCAD ou salve a seleção atual para reutilizar.": "Personal furniture catalog.\nImport FreeCAD projects or save the current selection for reuse.",
    "Gera o layout das pecas com a configuracao atual e cria resumo, plano de corte, alertas e retalhos.": "Generates the part layout with the current configuration and creates a summary, cut plan, alerts and remnants.",
    "Gera orçamento/cotação do projeto em HTML.\nInclui custo de material, fita de borda, ferragens e margem de lucro.": "Generates the project quote in HTML.\nIncludes material, edge band, hardware and profit margin costs.",
    "Gera layout de corte CNC usando o contorno real das pecas.\nIdeal para formas irregulares (circulos, curvas, L-shapes).\nNao altera o layout retangular existente.": "Generates a CNC cut layout using the real part contours.\nIdeal for irregular shapes (circles, curves, L-shapes).\nDoes not change the existing rectangular layout.",
    "Le os retalhos gerados pelo ultimo layout e atualiza o estoque de retalhos do documento, preservando os retalhos manuais.": "Reads remnants generated by the last layout and updates the document remnant stock while preserving manual remnants.",
    "Gera ou atualiza os codigos PN-xxx das pecas da selecao ou de todas as pecas visiveis.": "Creates or updates PN-xxx codes for selected parts or all visible parts.",
    "Abre o WoodCAM 2D na bancada PanelNest para pré-visualizar, simular e gerar G-code CNC.": "Opens WoodCAM 2D in the PanelNest workbench to preview, simulate and generate CNC G-code.",
    "Exporta etiquetas das peças: abre no navegador (com QR code), SVG individual ou ZPL para impressora Zebra.": "Exports part labels: opens them in a browser (with QR code), individual SVG or ZPL for Zebra printers.",
    "Visualizar, adicionar e importar retalhos do banco persistente (SQLite)": "View, add and import remnants from the persistent database (SQLite)",
    "Aplica material, metodo de corte, rotacao, veio/fibra e fita de borda nas pecas selecionadas ou visiveis.": "Applies material, cutting method, rotation, grain and edge banding to selected or visible parts.",
}


_PREFIX_EN = (
    ("Selecione ", "Select "),
    ("Selecionar ", "Select "),
    ("Importar ", "Import "),
    ("Exportar ", "Export "),
    ("Aplicar ", "Apply "),
    ("Gerar ", "Generate "),
    ("Criar ", "Create "),
    ("Editar ", "Edit "),
    ("Remover ", "Remove "),
    ("Adicionar ", "Add "),
    ("Atualizar ", "Update "),
    ("Mostrar ", "Show "),
    ("Nenhuma ", "No "),
    ("Nenhum ", "No "),
    ("Aviso", "Warning"),
    ("Erro", "Error"),
)

_PHRASES_EN = tuple(sorted(PT_EN.items(), key=lambda pair: len(pair[0]), reverse=True))


def _stored_language():
    try:
        import FreeCAD

        for path in (PARAMETER_PATH, WOODCAM_PARAMETER_PATH):
            value = FreeCAD.ParamGet(path).GetString(LANGUAGE_KEY, "")
            if value in {"pt", "en"}:
                return value
    except Exception:
        pass
    return "en"


_language = _stored_language()
_roots = []
_event_filter = None
_pending_show_translations = set()


def language():
    return _language


def _translate_command_fragment(value, code):
    """Match command resources after FreeCAD has normalized their whitespace."""

    text = "" if value is None else str(value)
    exact = PT_EN.get(text)
    if exact is not None:
        return exact
    normalized = " ".join(text.split())
    for source, target in PT_EN.items():
        if " ".join(str(source).split()) == normalized:
            return " ".join(str(target).split())
    return translate_text(text, code)


def _translate_freecad_command_tooltip(text, code):
    """Translate the rich tooltip synthesized by FreeCAD for commands.

    FreeCAD combines ``MenuText``, ``ToolTip`` and the command identifier into
    an HTML card. Long descriptions are split by paragraph tags at arbitrary
    word-wrap positions, so an exact catalogue sentence no longer exists as
    one contiguous substring. Recover the visible title/body, translate them,
    then rebuild only the body paragraph while preserving the command footer.
    """

    if "<b>" not in text or "<i>" not in text or "</p>" not in text:
        return None
    title_match = re.search(r"<b>(.*?)</b>", text, flags=re.DOTALL)
    footer_match = re.search(
        r"(<p[^>]*>\s*<i>.*?</i>\s*</p>\s*)$",
        text,
        flags=re.DOTALL,
    )
    if title_match is None or footer_match is None:
        return None
    header_end = text.find("</p>", title_match.end())
    if header_end < 0 or header_end >= footer_match.start():
        return None
    header_end += len("</p>")
    body_html = text[header_end : footer_match.start()]
    body_text = " ".join(
        html.unescape(re.sub(r"<[^>]+>", " ", body_html)).split()
    )
    if not body_text:
        return None
    title_text = html.unescape(title_match.group(1))
    translated_title = _translate_command_fragment(title_text, code)
    translated_body = _translate_command_fragment(body_text, code)
    header = (
        text[: title_match.start(1)]
        + html.escape(translated_title, quote=False)
        + text[title_match.end(1) : header_end]
    )
    body = (
        "<p style='white-space:normal; margin:0;'>%s</p>"
        % html.escape(translated_body, quote=False)
    )
    return header + body + footer_match.group(1)


def translate_text(value, code=None):
    text = "" if value is None else str(value)
    selected_code = code or _language
    if selected_code != "en" or not text:
        return text
    rich_tooltip = _translate_freecad_command_tooltip(text, selected_code)
    if rich_tooltip is not None:
        return rich_tooltip
    exact = PT_EN.get(text)
    if exact is not None:
        return exact
    for source, target in _PREFIX_EN:
        if text.startswith(source):
            return target + text[len(source) :]
    translated = text
    for source, target in _PHRASES_EN:
        if source and source in translated:
            translated = translated.replace(source, target)
    return translated


def _prop(obj, name, default=None):
    try:
        value = obj.property(name)
        return default if value is None else value
    except Exception:
        return default


def _set_source(obj, name, value):
    try:
        if _prop(obj, name) is None:
            obj.setProperty(name, "" if value is None else str(value))
    except Exception:
        pass


def _force_source(obj, name, value):
    try:
        obj.setProperty(name, "" if value is None else str(value))
    except Exception:
        pass


def _objects(root):
    result = [root]
    try:
        result.extend(root.findChildren(QtCore.QObject))
    except Exception:
        pass
    return [obj for obj in result if not _owned_by_embedded_translator(obj)]


def _owned_by_embedded_translator(obj):
    """Return whether a nested application owns this presentation subtree.

    PanelNest translates the FreeCAD workbench chrome and generic dialogs.
    WoodCAM has its own larger catalogue and marks its roots explicitly; if
    both timers rewrite those same widgets, tab labels alternate on screen.
    Walking the QObject parent chain keeps the boundary dependency-free and
    also covers actions and menus owned by the embedded application.
    """

    current = obj
    while current is not None:
        try:
            if bool(_prop(current, "woodcam_i18n_owned", False)):
                return True
            current = current.parent()
        except (RuntimeError, AttributeError, TypeError):
            return False
    return False


def _action_types():
    return tuple(
        value
        for value in (getattr(QtWidgets, "QAction", None), getattr(QtGui, "QAction", None))
        if value is not None
    )


def _translate_action(action):
    source = _prop(action, "panelnest_source_text")
    if source is None:
        source = action.text()
        _set_source(action, "panelnest_source_text", source)
    translated = translate_text(source)
    if action.text() != translated:
        action.setText(translated)
    for attr in ("toolTip", "statusTip", "whatsThis", "accessibleDescription"):
        getter = getattr(action, attr, None)
        setter = getattr(action, "set" + attr[0].upper() + attr[1:], None)
        if getter is None or setter is None:
            continue
        prop = "panelnest_source_" + attr
        source_value = _prop(action, prop)
        if source_value is None:
            source_value = getter()
            _set_source(action, prop, source_value)
        translated = translate_text(source_value)
        if getter() != translated:
            setter(translated)


def _translate_combo(combo):
    for index in range(combo.count()):
        source = combo.itemData(index, ITEM_SOURCE_ROLE)
        if source is None:
            source = combo.itemText(index)
            combo.setItemData(index, source, ITEM_SOURCE_ROLE)
        translated = translate_text(source)
        if combo.itemText(index) != translated:
            combo.setItemText(index, translated)


def _translate_tabs(tabs):
    for index in range(tabs.count()):
        source = tabs.tabBar().tabData(index)
        if hasattr(source, "toString"):
            try:
                source = source.toString()
            except Exception:
                pass
        if source is None or source == "":
            source = tabs.tabText(index)
        source = str(source)
        if tabs.tabBar().tabData(index) != source:
            tabs.tabBar().setTabData(index, source)
        translated = translate_text(source)
        if tabs.tabText(index) != translated:
            tabs.setTabText(index, translated)


def _sync_dynamic_sources(root):
    action_types = _action_types()
    for obj in _objects(root):
        try:
            if action_types and isinstance(obj, action_types):
                current = obj.text()
                source = _prop(obj, "panelnest_source_text")
                if source is not None and current not in {str(source), translate_text(source, "en")}:
                    _force_source(obj, "panelnest_source_text", current)
                continue
            if isinstance(obj, (QtWidgets.QAbstractButton, QtWidgets.QLabel, QtWidgets.QGroupBox, QtWidgets.QMenu)):
                getter = obj.title if isinstance(obj, QtWidgets.QMenu) else obj.text if hasattr(obj, "text") else obj.title
                current = getter()
                source = _prop(obj, "panelnest_source_text")
                if source is not None and current not in {str(source), translate_text(source, "en")}:
                    _force_source(obj, "panelnest_source_text", current)
        except (RuntimeError, AttributeError, TypeError):
            continue


def translate_widget_tree(root):
    if root is None:
        return
    _sync_dynamic_sources(root)
    action_types = _action_types()
    for obj in _objects(root):
        try:
            if action_types and isinstance(obj, action_types):
                _translate_action(obj)
                continue
            if isinstance(obj, QtWidgets.QComboBox):
                _translate_combo(obj)
            if isinstance(obj, QtWidgets.QTabWidget):
                _translate_tabs(obj)
            if isinstance(obj, QtWidgets.QMenu):
                source = _prop(obj, "panelnest_source_text")
                if source is None:
                    source = obj.title()
                    _set_source(obj, "panelnest_source_text", source)
                translated = translate_text(source)
                if obj.title() != translated:
                    obj.setTitle(translated)
            if isinstance(obj, QtWidgets.QGroupBox):
                source = _prop(obj, "panelnest_source_title")
                if source is None:
                    source = obj.title()
                    _set_source(obj, "panelnest_source_title", source)
                translated = translate_text(source)
                if obj.title() != translated:
                    obj.setTitle(translated)
            if isinstance(obj, QtWidgets.QAbstractButton):
                source = _prop(obj, "panelnest_source_text")
                if source is None:
                    source = obj.text()
                    _set_source(obj, "panelnest_source_text", source)
                translated = translate_text(source)
                if obj.text() != translated:
                    obj.setText(translated)
            if isinstance(obj, QtWidgets.QLabel):
                source = _prop(obj, "panelnest_source_text")
                if source is None:
                    source = obj.text()
                    _set_source(obj, "panelnest_source_text", source)
                translated = translate_text(source)
                if obj.text() != translated:
                    obj.setText(translated)
            if isinstance(obj, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
                getter = getattr(obj, "placeholderText", None)
                setter = getattr(obj, "setPlaceholderText", None)
                if getter is not None and setter is not None:
                    source = _prop(obj, "panelnest_source_placeholder")
                    if source is None:
                        source = getter()
                        _set_source(obj, "panelnest_source_placeholder", source)
                    translated = translate_text(source)
                    if getter() != translated:
                        setter(translated)
            if isinstance(obj, QtWidgets.QWidget):
                source = _prop(obj, "panelnest_source_window_title")
                if source is None:
                    source = obj.windowTitle()
                    if source:
                        _set_source(obj, "panelnest_source_window_title", source)
                if source:
                    translated = translate_text(source)
                    if obj.windowTitle() != translated:
                        obj.setWindowTitle(translated)
            for attr in ("toolTip", "statusTip", "whatsThis", "accessibleName"):
                getter = getattr(obj, attr, None)
                setter = getattr(obj, "set" + attr[0].upper() + attr[1:], None)
                if getter is None or setter is None:
                    continue
                prop = "panelnest_source_" + attr
                source = _prop(obj, prop)
                if source is None:
                    source = getter()
                    _set_source(obj, prop, source)
                if source:
                    translated = translate_text(source)
                    if getter() != translated:
                        setter(translated)
        except (RuntimeError, AttributeError, TypeError):
            continue


def _main_window():
    try:
        import FreeCADGui

        return FreeCADGui.getMainWindow()
    except Exception:
        return None


def _translate_shown_window(reference, key):
    _pending_show_translations.discard(key)
    try:
        root = reference()
    except Exception:
        root = None
    if root is None or _language != "en":
        return
    try:
        if root.isVisible():
            translate_widget_tree(root)
    except (RuntimeError, AttributeError, TypeError):
        pass


def _queue_shown_window(root):
    key = id(root)
    if key in _pending_show_translations:
        return
    try:
        reference = weakref.ref(root)
    except TypeError:
        return
    _pending_show_translations.add(key)
    QtCore.QTimer.singleShot(
        0,
        lambda current=reference, current_key=key: _translate_shown_window(
            current,
            current_key,
        ),
    )


class _TranslationEventFilter(QtCore.QObject):
    """Translate newly shown top-level windows without polling the whole GUI."""

    def eventFilter(self, obj, event):
        try:
            if (
                _language == "en"
                and event.type() == QtCore.QEvent.Show
                and isinstance(obj, QtWidgets.QWidget)
                and (obj.isWindow() or isinstance(obj, QtWidgets.QMenu))
                and not _owned_by_embedded_translator(obj)
            ):
                _queue_shown_window(obj)
        except (RuntimeError, AttributeError, TypeError):
            pass
        return False


def _ensure_event_filter():
    global _event_filter
    if _event_filter is not None:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    try:
        _event_filter = _TranslationEventFilter(app)
        app.installEventFilter(_event_filter)
    except Exception:
        _event_filter = None


def register_root(root):
    if root is None:
        return
    for reference in list(_roots):
        try:
            if reference() is root:
                translate_widget_tree(root)
                return
        except Exception:
            _roots.remove(reference)
    try:
        _roots.append(weakref.ref(root))
    except TypeError:
        _roots.append(lambda root=root: root)
    translate_widget_tree(root)
    _ensure_event_filter()


def set_language(code):
    global _language
    _language = "en" if str(code).lower().startswith("en") else "pt"
    try:
        import FreeCAD

        FreeCAD.ParamGet(PARAMETER_PATH).SetString(LANGUAGE_KEY, _language)
    except Exception:
        pass
    roots = []
    main = _main_window()
    if main is not None:
        roots.append(main)
    roots.extend(reference() for reference in _roots if reference() is not None)
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            roots.extend(app.topLevelWidgets())
        except Exception:
            pass
    for root in roots:
        translate_widget_tree(root)
    # When WoodCAM is already loaded inside this workbench, synchronize its
    # local fallback selector too.  Looking only in sys.modules avoids making
    # WoodCAM a dependency for PanelNest's lightweight startup.
    try:
        woodcam_i18n = sys.modules.get("woodcam_editor.presentation.i18n")
        if woodcam_i18n is not None and woodcam_i18n.language() != _language:
            woodcam_i18n.set_language(_language)
    except Exception:
        pass
    _ensure_event_filter()


def install_language_menu(main_window=None):
    main_window = main_window or _main_window()
    if main_window is None:
        return None
    menu_bar = main_window.menuBar()
    menu = menu_bar.findChild(QtWidgets.QMenu, "panelnestLanguageMenu")
    if menu is None:
        menu = menu_bar.addMenu("Idioma")
        menu.setObjectName("panelnestLanguageMenu")
        portuguese = menu.addAction("Português")
        english = menu.addAction("English")
        portuguese.triggered.connect(lambda: set_language("pt"))
        english.triggered.connect(lambda: set_language("en"))
    register_root(main_window)
    return menu


__all__ = [
    "install_language_menu",
    "language",
    "register_root",
    "set_language",
    "translate_text",
    "translate_widget_tree",
]
