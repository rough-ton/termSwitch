from conftest import pump

from test_aws_commands import data_dir, write_model  # noqa: F401  (fixture)


def make_pane(qapp, data_dir):
    from termswitch.aws_commands import build_catalog
    from termswitch.commands_pane import CommandsPane
    pane = CommandsPane()
    pane._on_loaded(build_catalog(data_dir, "2.99.0"))
    return pane


def rows(pane):
    m = pane.model
    return [m.data(m.index(i)) for i in range(m.rowCount())]


def test_lists_with_letter_headers(qapp, data_dir):
    pane = make_pane(qapp, data_dir)
    r = rows(pane)
    assert "E:" in r and "ec2 describe-instances" in r and "S:" in r
    header = pane.model.index(r.index("E:"))
    assert not (pane.model.flags(header) & pane.model.flags(header).ItemIsSelectable)


def test_service_and_name_filters(qapp, data_dir):
    pane = make_pane(qapp, data_dir)
    pane.service.setCurrentIndex(pane.service.findData("ec2"))
    assert rows(pane) == ["D:", "describe-db-things", "describe-instances"]
    assert all(not x.startswith("ec2 ") for x in rows(pane))   # service name hidden
    pane.service.setCurrentIndex(0)
    pane.name.setText("s3 cp")
    pane._refilter()
    assert rows(pane) == ["S:", "s3 cp"]
    pane.name.setText("caller")
    pane._refilter()
    assert rows(pane) == ["S:", "sts get-caller-identity"]


def test_insert_and_copy(qapp, data_dir):
    pane = make_pane(qapp, data_dir)
    got = []
    pane.insert_requested.connect(lambda text, run: got.append((text, run)))
    pane.name.setText("caller")
    pane._refilter()
    pane._select_first()
    assert pane.selected_command().cli == "aws sts get-caller-identity"
    assert not pane.btn_insert.isEnabled()          # no terminal yet
    pane.set_terminal_available(True)
    assert pane.btn_insert.isEnabled()
    pane.btn_insert.click()
    assert got == [("aws sts get-caller-identity ", False)]
    pane.btn_copy.click()
    from PySide6.QtGui import QGuiApplication
    assert QGuiApplication.clipboard().text() == "aws sts get-caller-identity"


def test_docs_url_is_https(qapp, data_dir):
    pane = make_pane(qapp, data_dir)
    urls = []
    pane.open_url_requested.connect(urls.append)
    pane.name.setText("describe-instances")
    pane._refilter()
    pane._select_first()
    pane.btn_docs.click()
    assert urls == ["https://awscli.amazonaws.com/v2/documentation/api/latest/reference/"
                    "ec2/describe-instances.html"]
