# Short-answer mini benchmark (fixture source)

`data/test.jsonl` holds `{id, question, answer}` records (split `test`).

The harness prompt for each record is:

    Answer the question below. Write only your final answer to `answer.txt`
    in the working directory `/app`.

    Question: <question>

`eval.py` reads `/app/answer.txt` (a missing file is an empty prediction) and
the gold answer, and prints `{"exact_match": 0.0 | 1.0}` after normalization.
The benchmark score is the mean `exact_match` over the split. No reference
solutions are distributed.
