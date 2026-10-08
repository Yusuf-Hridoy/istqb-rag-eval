"""Ask one question from the terminal."""

import argparse
import dataclasses
import json

from istqb_rag.pipeline import answer


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the ISTQB CTFL assistant one question.")
    parser.add_argument("question", help="The question to ask.")
    parser.add_argument("--json", action="store_true", help="Print the full RagResult as JSON.")
    args = parser.parse_args()

    result = answer(args.question)

    if args.json:
        print(json.dumps(dataclasses.asdict(result), indent=2))
        return

    print(f"Status: {result.status}")
    if result.error:
        print(f"Error: {result.error}")
    print(f"\n{result.answer}")
    print(f"\nCited pages: {result.cited_pages or '-'}")
    if result.contexts:
        print("\nRetrieved chunks:")
        for chunk in result.contexts:
            print(f"  {chunk.chunk_id} (p. {chunk.page}, score {chunk.score:.2f})")


if __name__ == "__main__":
    main()
