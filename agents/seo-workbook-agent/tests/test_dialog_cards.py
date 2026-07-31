from seo_workbook_agent.dialog_cards import (
    build_page_update_dialog,
    dialog_error,
    dialog_ok,
    extract_form_inputs,
    is_dialog_submission,
    is_slash_command,
)


def test_build_page_update_dialog_has_a_client_url_month_and_submit_button():
    dialog = build_page_update_dialog()
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]

    text_input_names = [w["textInput"]["name"] for w in widgets if "textInput" in w]
    assert "client" in text_input_names
    assert "month" in text_input_names
    assert "url" in text_input_names
    assert "headings" in text_input_names
    assert "notes" in text_input_names

    button_widgets = [w for w in widgets if "buttonList" in w]
    assert len(button_widgets) == 1
    assert button_widgets[0]["buttonList"]["buttons"][0]["onClick"]["action"]["function"] == "submitPageUpdate"


def test_build_page_update_dialog_marks_headings_and_notes_multiline():
    dialog = build_page_update_dialog()
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["headings"]["type"] == "MULTIPLE_LINE"
    assert by_name["notes"]["type"] == "MULTIPLE_LINE"
    assert "type" not in by_name["client"]


def test_dialog_ok_closes_with_ok_status():
    result = dialog_ok("Recorded!")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "OK"
    assert status["userFacingMessage"] == "Recorded!"


def test_dialog_error_closes_with_invalid_argument_status():
    result = dialog_error("Missing url")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert status["userFacingMessage"] == "Missing url"


def test_extract_form_inputs_pulls_string_values():
    event = {
        "common": {
            "formInputs": {
                "client": {"stringInputs": {"value": ["North Texas Trailers"]}},
                "month": {"stringInputs": {"value": ["2026-07"]}},
            }
        }
    }
    assert extract_form_inputs(event) == {"client": "North Texas Trailers", "month": "2026-07"}


def test_extract_form_inputs_handles_a_blank_field():
    event = {"common": {"formInputs": {"notes": {"stringInputs": {"value": []}}}}}
    assert extract_form_inputs(event) == {"notes": ""}


def test_extract_form_inputs_returns_empty_dict_with_no_form_inputs():
    assert extract_form_inputs({}) == {}


def test_is_slash_command_true_when_message_has_slash_command():
    event = {"type": "MESSAGE", "message": {"slashCommand": {"commandId": "1"}}}
    assert is_slash_command(event) is True


def test_is_slash_command_false_for_a_plain_message():
    event = {"type": "MESSAGE", "message": {"text": "hi"}}
    assert is_slash_command(event) is False


def test_is_dialog_submission_true_for_submit_dialog_event():
    event = {"type": "CARD_CLICKED", "dialogEventType": "SUBMIT_DIALOG"}
    assert is_dialog_submission(event) is True


def test_is_dialog_submission_false_for_other_card_clicked_events():
    event = {"type": "CARD_CLICKED", "dialogEventType": "UNKNOWN"}
    assert is_dialog_submission(event) is False
