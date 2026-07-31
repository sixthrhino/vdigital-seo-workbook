from seo_workbook_agent.dialog_cards import (
    build_client_month_dialog,
    build_url_entry_dialog,
    build_url_only_dialog,
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


def test_build_url_only_dialog_has_just_a_url_field_and_a_next_button():
    dialog = build_url_only_dialog("KYZ", "2026-06", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]

    text_input_names = [w["textInput"]["name"] for w in widgets if "textInput" in w]
    assert text_input_names == ["url"]

    button_widgets = [w for w in widgets if "buttonList" in w]
    assert len(button_widgets) == 1
    button = button_widgets[0]["buttonList"]["buttons"][0]
    assert button["onClick"]["action"]["function"] == "fetchPageAndContinue"
    params = {p["key"]: p["value"] for p in button["onClick"]["action"]["parameters"]}
    assert params == {"client": "KYZ", "month": "2026-06", "count": "1"}


def test_build_url_only_dialog_header_confirms_the_last_saved_url():
    dialog = build_url_only_dialog("KYZ", "2026-06", count=2, last_saved_url="https://kyz.com/a/")
    header = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["header"]
    assert header == "✓ Saved https://kyz.com/a/ — Page 2 for KYZ (2026-06)"


def test_build_url_entry_dialog_has_the_full_page_field_set_minus_url_and_old_values():
    dialog = build_url_entry_dialog("North Texas Trailers", "2026-07", "https://kyz.com/a/", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]

    text_input_names = [w["textInput"]["name"] for w in widgets if "textInput" in w]
    assert "client" not in text_input_names
    assert "month" not in text_input_names
    assert "url" not in text_input_names
    assert "title_old" not in text_input_names
    assert "meta_old" not in text_input_names
    assert "h1_old" not in text_input_names
    assert "title_new" in text_input_names
    assert "headings" in text_input_names
    assert "notes" in text_input_names


def test_build_url_entry_dialog_shows_the_url_being_edited():
    dialog = build_url_entry_dialog("KYZ", "2026-06", "https://kyz.com/a/", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    assert widgets[0]["textParagraph"]["text"] == "Editing: https://kyz.com/a/"


def test_build_url_entry_dialog_marks_headings_and_notes_multiline():
    dialog = build_url_entry_dialog("KYZ", "2026-06", "https://kyz.com/a/", count=1)
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["headings"]["type"] == "MULTIPLE_LINE"
    assert by_name["notes"]["type"] == "MULTIPLE_LINE"
    assert "type" not in by_name["title_new"]


def test_build_url_entry_dialog_shows_fetched_current_values_as_hint_text():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", "https://kyz.com/a/", count=1,
        current_values={"title": "Old Title Tag", "meta_description": "Old meta.", "h1": "Old H1"},
    )
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["title_new"]["hintText"] == "Current: Old Title Tag"
    assert by_name["meta_new"]["hintText"] == "Current: Old meta."
    assert by_name["h1_new"]["hintText"] == "Current: Old H1"


def test_build_url_entry_dialog_truncates_a_long_current_value_hint():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", "https://kyz.com/a/", count=1,
        current_values={"meta_description": "x" * 300},
    )
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    hint = by_name["meta_new"]["hintText"]
    assert hint.startswith("Current: ")
    assert hint.endswith("…")
    assert len(hint) <= len("Current: ") + 150


def test_build_url_entry_dialog_falls_back_to_the_static_hint_with_no_fetched_value():
    dialog = build_url_entry_dialog("KYZ", "2026-06", "https://kyz.com/a/", count=1, current_values={})
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert "Current:" not in by_name["keyword"].get("hintText", "")
    assert "hintText" not in by_name["title_new"]


def test_build_url_entry_dialog_carries_client_month_url_count_and_current_values_as_button_parameters():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", "https://kyz.com/a/", count=3,
        current_values={"title": "Old Title", "meta_description": "Old meta", "h1": "Old H1"},
    )
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    buttons = next(w for w in widgets if "buttonList" in w)["buttonList"]["buttons"]

    by_text = {b["text"]: b for b in buttons}
    assert set(by_text) == {"Next URL", "Done"}
    for button in by_text.values():
        params = {p["key"]: p["value"] for p in button["onClick"]["action"]["parameters"]}
        assert params == {
            "client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "3",
            "current_title": "Old Title", "current_meta_description": "Old meta", "current_h1": "Old H1",
        }

    assert by_text["Next URL"]["onClick"]["action"]["function"] == "saveAndContinue"
    assert by_text["Done"]["onClick"]["action"]["function"] == "saveAndFinish"


def test_build_url_entry_dialog_header_shows_client_month_and_count():
    dialog = build_url_entry_dialog("KYZ", "2026-06", "https://kyz.com/a/", count=2)
    header = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["header"]
    assert header == "Page 2 for KYZ (2026-06)"


def test_build_url_entry_dialog_prefills_fields_from_a_previous_attempt():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", "https://kyz.com/a/", count=1,
        prefill={"title_new": "x" * 91},
    )
    widgets = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]["widgets"]
    by_name = {w["textInput"]["name"]: w["textInput"] for w in widgets if "textInput" in w}
    assert by_name["title_new"]["value"] == "x" * 91
    assert "value" not in by_name["geo"]


def test_build_url_entry_dialog_shows_error_text_and_header_when_given():
    dialog = build_url_entry_dialog(
        "KYZ", "2026-06", "https://kyz.com/a/", count=3,
        error_text="⚠️ title_tag: title tag is 91 characters, must be 60 or fewer (brand name excluded)",
    )
    section = dialog["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert section["header"] == "⚠️ Please fix and resubmit — Page 3 for KYZ (2026-06)"
    assert section["widgets"][0]["textParagraph"]["text"] == (
        "⚠️ title_tag: title tag is 91 characters, must be 60 or fewer (brand name excluded)"
    )


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
