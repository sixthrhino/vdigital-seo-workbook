from seo_workbook_agent.dialog_cards import (
    build_client_month_dialog,
    build_url_entry_dialog,
    dialog_error,
    dialog_ok,
    extract_button_parameters,
    extract_form_inputs,
    invoked_function,
    is_dialog_submission,
    is_slash_command,
)


def test_build_client_month_dialog_has_client_and_month_fields_and_a_next_button():
    dialog = build_client_month_dialog()
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]

    text_input_names = [w["textInput"]["name"] for w in widgets if "textInput" in w]
    assert text_input_names == ["client", "month"]

    button_widgets = [w for w in widgets if "buttonList" in w]
    assert len(button_widgets) == 1
    assert button_widgets[0]["buttonList"]["buttons"][0]["onClick"]["action"]["function"] == "startPageEntry"


def test_build_url_entry_dialog_has_the_full_page_field_set():
    dialog = build_url_entry_dialog("North Texas Trailers", "2026-07", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]

    text_input_names = [w["textInput"]["name"] for w in widgets if "textInput" in w]
    assert "client" not in text_input_names
    assert "month" not in text_input_names
    assert "url" in text_input_names
    assert "headings" in text_input_names
    assert "notes" in text_input_names


def test_build_url_entry_dialog_marks_headings_and_notes_multiline():
    dialog = build_url_entry_dialog("KYZ", "2026-06", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["headings"]["type"] == "MULTIPLE_LINE"
    assert by_name["notes"]["type"] == "MULTIPLE_LINE"
    assert "type" not in by_name["url"]


def test_build_url_entry_dialog_carries_client_month_count_as_button_parameters():
    dialog = build_url_entry_dialog("KYZ", "2026-06", count=3)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    buttons = next(w for w in widgets if "buttonList" in w)["buttonList"]["buttons"]

    by_text = {b["text"]: b for b in buttons}
    assert set(by_text) == {"Next URL", "Done"}
    for button in by_text.values():
        params = {p["key"]: p["value"] for p in button["onClick"]["action"]["parameters"]}
        assert params == {"client": "KYZ", "month": "2026-06", "count": "3"}

    assert by_text["Next URL"]["onClick"]["action"]["function"] == "saveAndContinue"
    assert by_text["Done"]["onClick"]["action"]["function"] == "saveAndFinish"


def test_build_url_entry_dialog_header_shows_client_month_and_count():
    dialog = build_url_entry_dialog("KYZ", "2026-06", count=2)
    header = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["header"]
    assert header == "Page 2 for KYZ (2026-06)"


def test_build_url_entry_dialog_header_confirms_the_last_saved_url():
    dialog = build_url_entry_dialog("KYZ", "2026-06", count=2, last_saved_url="https://kyz.com/a/")
    header = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["header"]
    assert header.startswith("✓ Saved https://kyz.com/a/")


def test_build_url_entry_dialog_prefills_fields_from_a_previous_attempt():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", count=1,
        prefill={"url": "https://kyz.com/a/", "title_new": "x" * 91},
    )
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["url"]["value"] == "https://kyz.com/a/"
    assert by_name["title_new"]["value"] == "x" * 91
    assert "value" not in by_name["geo"]


def test_build_url_entry_dialog_shows_error_text_and_header_when_given():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", count=3,
        error_text="⚠️ title_tag: title tag is 91 characters, must be 60 or fewer (brand name excluded)",
    )
    section = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert section["header"] == "⚠️ Please fix and resubmit — Page 3 for KYZ (2026-06)"
    assert section["widgets"][0]["textParagraph"]["text"] == (
        "⚠️ title_tag: title tag is 91 characters, must be 60 or fewer (brand name excluded)"
    )


def test_build_url_entry_dialog_error_text_takes_priority_over_last_saved_url():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", count=1, last_saved_url="https://kyz.com/prev/", error_text="⚠️ something failed"
    )
    header = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["header"]
    assert header.startswith("⚠️ Please fix and resubmit")


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


def test_invoked_function_reads_common_invoked_function():
    assert invoked_function({"common": {"invokedFunction": "saveAndContinue"}}) == "saveAndContinue"


def test_invoked_function_falls_back_to_action_method_name():
    assert invoked_function({"action": {"actionMethodName": "saveAndFinish"}}) == "saveAndFinish"


def test_invoked_function_none_when_absent():
    assert invoked_function({}) is None


def test_extract_button_parameters_from_common_parameters_map():
    event = {"common": {"parameters": {"client": "KYZ", "month": "2026-06"}}}
    assert extract_button_parameters(event) == {"client": "KYZ", "month": "2026-06"}


def test_extract_button_parameters_from_action_parameters_list():
    event = {"action": {"parameters": [{"key": "client", "value": "KYZ"}, {"key": "month", "value": "2026-06"}]}}
    assert extract_button_parameters(event) == {"client": "KYZ", "month": "2026-06"}


def test_extract_button_parameters_empty_when_absent():
    assert extract_button_parameters({}) == {}


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
