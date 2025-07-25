import numpy as np
import torch

from typing import Dict, List, Tuple

from .stat_calculator import StatCalculator
from lm_polygraph.utils.model import WhiteboxModel
from lm_polygraph.utils.deberta import Deberta
from collections import defaultdict
import torch.nn as nn
import string


def eval_nli_model(
    nli_queue: List[Tuple[str, str]],
    deberta: Deberta,
) -> List[Dict[str, float]]:
    nli_set = list(set(nli_queue))

    softmax = nn.Softmax(dim=1)
    w_probs = defaultdict(lambda: defaultdict(lambda: None))
    with torch.no_grad():
        for k in range(0, len(nli_set), deberta.batch_size):
            batch = nli_set[k : k + deberta.batch_size]
            encoded = deberta.deberta_tokenizer.batch_encode_plus(
                batch, padding=True, return_tensors="pt"
            ).to(deberta.device)
            logits = deberta.deberta(**encoded).logits.detach()
            for (wi, wj), prob in zip(batch, softmax(logits).cpu()):
                w_probs[wi][wj] = prob

    classes = []
    for w1, w2 in nli_queue:
        pr = w_probs[w1][w2]
        ent_id = deberta.deberta.config.label2id["ENTAILMENT"]
        contra_id = deberta.deberta.config.label2id["CONTRADICTION"]
        neutral_id = deberta.deberta.config.label2id["NEUTRAL"]
        classes.append(
            {
                "entail": pr[ent_id].item(),
                "contra": pr[contra_id].item(),
                "neutral": pr[neutral_id].item(),
            }
        )
    return classes


class GreedyAlternativesNLICalculator(StatCalculator):
    @staticmethod
    def meta_info() -> Tuple[List[str], List[str]]:
        """
        Returns the statistics and dependencies for the SamplingPromptCalculator.
        """
        return ["greedy_tokens_alternatives_nli"], ["greedy_tokens_alternatives"]

    def __init__(self, nli_model):
        super().__init__()
        self.nli_model = nli_model

    def _strip(self, w: str):
        return w.strip(string.punctuation + " \n")

    def __call__(
        self,
        dependencies: Dict[str, np.array],
        texts: List[str],
        model: WhiteboxModel,
        max_new_tokens: int = 100,
        **kwargs,
    ) -> Dict[str, np.ndarray]:
        greedy_alternatives = dependencies["greedy_tokens_alternatives"]
        greedy_alternatives_nli = []
        for sample_alternatives in greedy_alternatives:
            nli_matrixes = []
            for w_number, word_alternatives in enumerate(sample_alternatives):
                nli_queue = []
                nli_matrix = [
                    ["" for _ in range(len(word_alternatives))]
                    for _ in range(len(word_alternatives))
                ]
                if len(word_alternatives) > 0 and not isinstance(
                    word_alternatives[0][0],
                    str,
                ):
                    word_alternatives = [
                        (model.tokenizer.decode([alt]), prob)
                        for alt, prob in word_alternatives
                    ]
                words = [self._strip(alt[0]) for alt in word_alternatives]
                for wi in words:
                    nli_queue.append((words[0], wi))
                    nli_queue.append((wi, words[0]))

                nli_classes: List[Dict[str, float]] = eval_nli_model(
                    nli_queue,
                    self.nli_model,
                )
                nli_class: Dict[Tuple[str, str], str | None] = defaultdict(lambda: None)
                for nli_cl, (w1, w2) in zip(nli_classes, nli_queue):
                    nli_class[w1, w2] = max(nli_cl, key=nli_cl.get)

                for i, wi in enumerate(words):
                    for j, wj in enumerate(words):
                        # Only calculate NLI with greedy token
                        if i > 0 and j > 0:
                            continue
                        nli_matrix[i][j] = nli_class[wi, wj]

                nli_matrixes.append(nli_matrix)
            greedy_alternatives_nli.append(nli_matrixes)

        return {"greedy_tokens_alternatives_nli": greedy_alternatives_nli}


class GreedyAlternativesFactPrefNLICalculator(StatCalculator):
    @staticmethod
    def meta_info() -> Tuple[List[str], List[str]]:
        """
        Returns the statistics and dependencies for the SamplingPromptCalculator.
        """

        return ["greedy_tokens_alternatives_fact_pref_nli"], [
            "greedy_tokens_alternatives",
            "greedy_tokens",
            "claims",
        ]

    def __init__(self, nli_model):
        super().__init__()
        self.nli_model = nli_model

    def _strip(self, w: str):
        return w.strip(string.punctuation + " \n")

    def __call__(
        self,
        dependencies: Dict[str, np.array],
        texts: List[str],
        model: WhiteboxModel,
        max_new_tokens: int = 100,
        **kwargs,
    ) -> Dict[str, np.ndarray]:
        greedy_alternatives = dependencies["greedy_tokens_alternatives"]
        greedy_tokens = dependencies["greedy_tokens"]
        claims = dependencies["claims"]
        greedy_alternatives_nli = []
        for sample_alternatives, sample_claims, sample_tokens in zip(
            greedy_alternatives,
            claims,
            greedy_tokens,
        ):
            nli_queue = []
            for claim in sample_claims:
                tokens = [sample_tokens[t] for t in claim.aligned_token_ids]
                alts = [sample_alternatives[t] for t in claim.aligned_token_ids]
                for i in range(len(tokens)):
                    for j in range(len(alts[i])):
                        text1 = model.tokenizer.decode(tokens[: i + 1])
                        text2 = model.tokenizer.decode(tokens[:i] + [alts[i][j][0]])
                        nli_queue.append((text1, text2))
                        nli_queue.append((text2, text1))

            nli_classes: List[Dict[str, float]] = eval_nli_model(
                nli_queue,
                self.nli_model,
            )

            nli_matrixes: List[List[List[List[str | None]]]] = []
            for claim in sample_claims:
                nli_matrixes.append([])
                tokens = [sample_tokens[t] for t in claim.aligned_token_ids]
                alts = [sample_alternatives[t] for t in claim.aligned_token_ids]
                for i in range(len(tokens)):
                    nli_matrix: List[List[str | None]] = []
                    for _ in range(len(alts[i])):
                        nli_matrix.append([])
                        for j in range(len(alts[i])):
                            nli_matrix[-1].append(None)
                    for j in range(len(alts[i])):
                        nli_matrix[0][j] = max(nli_classes[0], key=nli_classes[0].get)
                        nli_matrix[j][0] = max(nli_classes[1], key=nli_classes[1].get)
                        nli_classes = nli_classes[2:]
                    nli_matrixes[-1].append(nli_matrix)
            greedy_alternatives_nli.append(nli_matrixes)

        return {"greedy_tokens_alternatives_fact_pref_nli": greedy_alternatives_nli}
