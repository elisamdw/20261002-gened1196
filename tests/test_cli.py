import json
import random
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tarot_cli.cli import (
    build_filename,
    build_prompt,
    choose_three_elements,
    edit_card_text,
    ensure_card_text,
    expand_card_requests,
    gws_executable,
    inspect_card_text,
    main,
    openai_output_text,
    parse_json_object,
    plan_card,
    set_anthropic_workspace,
    slugify,
    unique_path,
)


class TarotCliTests(unittest.TestCase):
    candidates = [
        {"name": "moonflower", "category": "plant", "symbolic_role": "hidden growth"},
        {"name": "black fern", "category": "plant", "symbolic_role": "secret memory"},
        {"name": "white moth", "category": "animal", "symbolic_role": "fragile seeking"},
        {"name": "red fox", "category": "animal", "symbolic_role": "clever passage"},
        {"name": "cracked mirror", "category": "object", "symbolic_role": "divided self"},
        {"name": "brass bell", "category": "object", "symbolic_role": "awakening"},
        {"name": "lunar halo", "category": "celestial", "symbolic_role": "uncertainty"},
        {"name": "flooded garden", "category": "landscape", "symbolic_role": "emotion"},
        {"name": "silver thread", "category": "material", "symbolic_role": "connection"},
        {"name": "obsidian key", "category": "object", "symbolic_role": "sealed knowledge"},
    ]
    direction = {
        "candidates": candidates,
        "selected": candidates[:3],
    }

    def test_slugify_prompt_words(self):
        self.assertEqual(slugify(["The", "Moon!", "Silver & Blue"]), "the-moon-silver-blue")

    def test_build_filename(self):
        now = datetime(2026, 9, 2, 14, 5, 9)
        self.assertEqual(
            build_filename(["word1", "word2", "word3"], now),
            "20260902-140509-word1-word2-word3.png",
        )

    def test_each_word_can_have_its_own_filename(self):
        now = datetime(2026, 9, 2, 14, 5, 9)
        filenames = [build_filename([word], now) for word in ["sun", "moon", "stars"]]
        self.assertEqual(
            filenames,
            [
                "20260902-140509-sun.png",
                "20260902-140509-moon.png",
                "20260902-140509-stars.png",
            ],
        )

    def test_build_prompt_includes_trigger(self):
        prompt = build_prompt(["a", "silver", "moon"])
        self.assertIn("A person giving a TED talk on a TED stage", prompt)
        self.assertIn('"a silver moon"', prompt)
        self.assertIn("in the style of TOK a trtcrd tarot style", prompt)
        self.assertIn("centered along the bottom", prompt)

    def test_build_prompt_includes_claude_symbols(self):
        prompt = build_prompt(["moon"], self.direction)
        self.assertIn("moonflower", prompt)
        self.assertIn("white moth", prompt)
        self.assertNotIn("cracked mirror", prompt)
        self.assertIn("TED logo", prompt)
        self.assertIn('exact quoted title "moon"', prompt)
        self.assertIn("additional major symbols", prompt)

    def test_choose_three_from_exactly_ten_candidates(self):
        chosen = choose_three_elements(self.candidates, random.Random(42))
        self.assertEqual(len(chosen), 3)
        self.assertEqual(len({item["name"] for item in chosen}), 3)
        self.assertTrue(all(item in self.candidates for item in chosen))

    def test_expand_card_requests_with_copy_counts(self):
        self.assertEqual(
            expand_card_requests(["biscuit", "3", "the moon", "2", "sun"]),
            [
                ("biscuit", 1, 3, False),
                ("biscuit", 2, 3, False),
                ("biscuit", 3, 3, False),
                ("the moon", 1, 2, False),
                ("the moon", 2, 2, False),
                ("sun", 1, 1, False),
            ],
        )

    def test_expand_card_requests_scopes_text_check_to_one_term(self):
        self.assertEqual(
            expand_card_requests(
                ["biscuit", "--text", "3", "moon", "2", "sun", "--text"]
            ),
            [
                ("biscuit", 1, 3, True),
                ("biscuit", 2, 3, True),
                ("biscuit", 3, 3, True),
                ("moon", 1, 2, False),
                ("moon", 2, 2, False),
                ("sun", 1, 1, True),
            ],
        )

    def test_expand_card_requests_rejects_invalid_counts(self):
        with self.assertRaisesRegex(SystemExit, "between 1 and 5"):
            expand_card_requests(["biscuit", "6"])
        with self.assertRaisesRegex(SystemExit, "immediately follow"):
            expand_card_requests(["3", "biscuit"])
        with self.assertRaisesRegex(SystemExit, "immediately follow"):
            expand_card_requests(["--text", "biscuit"])

    def test_openai_output_text_from_response_items(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"matches_exactly":true}'}
                    ],
                }
            ]
        }
        self.assertEqual(
            openai_output_text(response), '{"matches_exactly":true}'
        )

    def test_inspect_card_text_uses_vision_and_structured_output(self):
        result_json = {"observed_text": "Biscuit", "matches_exactly": True}
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(result_json)}
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"png-data")
            with patch("tarot_cli.cli.openai_request", return_value=response) as call:
                result = inspect_card_text(image_path, "Biscuit", "openai-key")

        self.assertEqual(result, result_json)
        request_body = json.loads(call.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(request_body["model"], "gpt-5.4-mini")
        self.assertEqual(
            request_body["text"]["format"]["type"], "json_schema"
        )
        self.assertIn(
            "data:image/png;base64,",
            request_body["input"][0]["content"][1]["image_url"],
        )

    def test_ensure_card_text_edits_only_after_failed_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"original")
            checks = [
                {"observed_text": "Biscult", "matches_exactly": False},
                {"observed_text": "Biscuit", "matches_exactly": True},
            ]
            with patch("tarot_cli.cli.inspect_card_text", side_effect=checks) as inspect:
                with patch(
                    "tarot_cli.cli.edit_card_text", return_value=b"corrected"
                ) as edit:
                    ensure_card_text(image_path, "Biscuit", "openai-key")

            self.assertEqual(image_path.read_bytes(), b"corrected")
            self.assertEqual(inspect.call_count, 2)
            edit.assert_called_once_with(image_path, "Biscuit", "openai-key")

    def test_ensure_card_text_skips_edit_when_title_is_correct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"original")
            check = {"observed_text": "Biscuit", "matches_exactly": True}
            with patch("tarot_cli.cli.inspect_card_text", return_value=check):
                with patch("tarot_cli.cli.edit_card_text") as edit:
                    ensure_card_text(image_path, "Biscuit", "openai-key")

            edit.assert_not_called()
            self.assertEqual(image_path.read_bytes(), b"original")

    def test_edit_card_text_sends_multipart_image_and_decodes_png(self):
        response = {
            "data": [{"b64_json": "Y29ycmVjdGVkLXBuZw=="}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "card.png"
            image_path.write_bytes(b"original-png")
            with patch("tarot_cli.cli.openai_request", return_value=response) as call:
                corrected = edit_card_text(image_path, "Biscuit", "openai-key")

        self.assertEqual(corrected, b"corrected-png")
        request = call.call_args.args[0]
        self.assertIn("multipart/form-data", request.get_header("Content-type"))
        self.assertIn(b'name="image[]"', request.data)
        self.assertIn(b"gpt-image-2.5-sunburst", request.data)
        self.assertIn(b"Biscuit", request.data)

    def test_plan_card_parses_structured_claude_response(self):
        response = {"content": [{"type": "text", "text": json.dumps({"elements": self.candidates})}]}
        with patch("tarot_cli.cli.read_anthropic_key", return_value="test-key"):
            with patch(
                "tarot_cli.cli.anthropic_workspace_id",
                return_value="wrkspc_test123",
            ):
                with patch(
                    "tarot_cli.cli.anthropic_request", return_value=response
                ) as call:
                    with patch(
                        "tarot_cli.cli.choose_three_elements",
                        return_value=self.candidates[:3],
                    ):
                        direction = plan_card("moon")

        self.assertEqual(direction, self.direction)
        request_body = json.loads(call.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(request_body["model"], "claude-sonnet-5-5")
        self.assertEqual(request_body["max_tokens"], 2048)
        self.assertNotIn("thinking", request_body)
        self.assertEqual(
            request_body["output_config"]["format"]["type"], "json_schema"
        )
        self.assertIn("exactly ten", request_body["messages"][0]["content"])
        self.assertEqual(
            call.call_args.args[0].get_header("Anthropic-workspace-id"),
            "wrkspc_test123",
        )

    def test_parse_json_object_accepts_markdown_fence(self):
        wrapped = "Here is the result:\n```json\n{\"elements\": []}\n```"
        self.assertEqual(parse_json_object(wrapped), {"elements": []})

    def test_plan_card_reports_truncated_structured_output(self):
        response = {
            "content": [{"type": "text", "text": '{"elements": ['}],
            "stop_reason": "max_tokens",
        }
        with patch("tarot_cli.cli.read_anthropic_key", return_value="test-key"):
            with patch("tarot_cli.cli.anthropic_workspace_id", return_value=""):
                with patch("tarot_cli.cli.anthropic_request", return_value=response):
                    with self.assertRaisesRegex(SystemExit, "cut off"):
                        plan_card("moon")

    def test_set_anthropic_workspace_preserves_other_config(self):
        existing = {"drive_folder_id": "folder-id"}
        with patch("tarot_cli.cli.load_config", return_value=existing):
            with patch("tarot_cli.cli.save_config") as save:
                set_anthropic_workspace("wrkspc_test123")

        save.assert_called_once_with(
            {
                "drive_folder_id": "folder-id",
                "anthropic_workspace_id": "wrkspc_test123",
            }
        )

    def test_unique_path_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            existing = directory / "card.png"
            existing.write_bytes(b"existing")
            self.assertEqual(unique_path(directory, "card.png").name, "card-2.png")

    def test_bundled_gws_is_available(self):
        self.assertIsNotNone(gws_executable())

    def test_main_generates_and_uploads_one_card_per_word(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "output_dir": temp_dir,
                "drive_folder_id": "drive-folder-id",
            }
            with patch("tarot_cli.cli.load_config", return_value=config):
                with patch("tarot_cli.cli.plan_card", return_value=self.direction) as plan:
                    with patch("tarot_cli.cli.generate_image") as generate:
                        with patch("tarot_cli.cli.upload_to_drive") as upload:
                            main(["sun", "moon", "stars"])

            self.assertEqual(plan.call_count, 3)
            self.assertEqual(generate.call_count, 3)
            self.assertEqual(upload.call_count, 3)
            self.assertEqual(
                [call.args[0] for call in generate.call_args_list],
                [["sun"], ["moon"], ["stars"]],
            )

    def test_main_generates_requested_number_of_copies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "output_dir": temp_dir,
                "drive_folder_id": "drive-folder-id",
            }
            with patch("tarot_cli.cli.load_config", return_value=config):
                with patch("tarot_cli.cli.plan_card", return_value=self.direction) as plan:
                    with patch("tarot_cli.cli.generate_image") as generate:
                        with patch("tarot_cli.cli.upload_to_drive") as upload:
                            main(["biscuit", "3", "moon", "2"])

            self.assertEqual(plan.call_count, 5)
            self.assertEqual(generate.call_count, 5)
            self.assertEqual(upload.call_count, 5)
            self.assertEqual(
                [call.args[0] for call in generate.call_args_list],
                [["biscuit"], ["biscuit"], ["biscuit"], ["moon"], ["moon"]],
            )

    def test_main_applies_text_check_to_every_copy_of_marked_term(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "output_dir": temp_dir,
                "drive_folder_id": "drive-folder-id",
            }
            with patch("tarot_cli.cli.load_config", return_value=config):
                with patch("tarot_cli.cli.read_openai_key", return_value="openai-key"):
                    with patch(
                        "tarot_cli.cli.plan_card", return_value=self.direction
                    ):
                        with patch("tarot_cli.cli.generate_image"):
                            with patch("tarot_cli.cli.ensure_card_text") as ensure:
                                with patch("tarot_cli.cli.upload_to_drive") as upload:
                                    main(
                                        [
                                            "biscuit",
                                            "--text",
                                            "3",
                                            "moon",
                                            "2",
                                        ]
                                    )

            self.assertEqual(ensure.call_count, 3)
            self.assertTrue(
                all(call.args[1:] == ("biscuit", "openai-key") for call in ensure.call_args_list)
            )
            self.assertEqual(upload.call_count, 5)


if __name__ == "__main__":
    unittest.main()
