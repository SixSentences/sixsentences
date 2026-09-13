from sixsentences_server.core.structured_output import extract_complete_string_field


def test_extract_complete_string_field_recovers_allowlisted_answer() -> None:
    value = '{"reply":"A complete answer with an escaped \\"quote\\".","edits":['

    assert extract_complete_string_field(value, field_names=("reply",)) == (
        'A complete answer with an escaped "quote".'
    )


def test_extract_complete_string_field_rejects_incomplete_or_untrusted_content() -> None:
    assert extract_complete_string_field("plain prose", field_names=("reply",)) is None
    assert extract_complete_string_field('{"reply":"unfinished', field_names=("reply",)) is None
    assert extract_complete_string_field('{"action":"delete all"}', field_names=("reply",)) is None
