import csv
from typing import Dict, Optional
import logging
import copy

from overrides import overrides

from allennlp.common.checks import ConfigurationError
from allennlp.common.file_utils import cached_path
from allennlp.common.util import START_SYMBOL, END_SYMBOL
from allennlp.data.dataset_readers.dataset_reader import DatasetReader
from allennlp.data.fields import TextField
from allennlp.data.instance import Instance
from allennlp.data.tokenizers import Tokenizer, SpacyTokenizer, Token
from allennlp.data.token_indexers import TokenIndexer, SingleIdTokenIndexer

logger = logging.getLogger(__name__)


@DatasetReader.register("seq2seq")
class Seq2SeqDatasetReader(DatasetReader):
    """
    Read a tsv file containing paired sequences, and create a dataset suitable for a
    `ComposedSeq2Seq` model, or any model with a matching API.

    Expected format for each input line: <source_sequence_string>\t<target_sequence_string>

    The output of `read` is a list of `Instance` s with the fields:
        source_tokens : `TextField` and
        target_tokens : `TextField`

    `START_SYMBOL` and `END_SYMBOL` tokens are added to the source and target sequences.

    # Parameters

    source_tokenizer : `Tokenizer`, optional
        Tokenizer to use to split the input sequences into words or other kinds of tokens. Defaults
        to `SpacyTokenizer()`.
    target_tokenizer : `Tokenizer`, optional
        Tokenizer to use to split the output sequences (during training) into words or other kinds
        of tokens. Defaults to `source_tokenizer`.
    source_token_indexers : `Dict[str, TokenIndexer]`, optional
        Indexers used to define input (source side) token representations. Defaults to
        `{"tokens": SingleIdTokenIndexer()}`.
    target_token_indexers : `Dict[str, TokenIndexer]`, optional
        Indexers used to define output (target side) token representations. Defaults to
        `source_token_indexers`.
    source_add_start_token : `bool`, (optional, default=`True`)
        Whether or not to add `start_symbol` to the beginning of the source sequence.
    source_add_end_token : `bool`, (optional, default=`True`)
        Whether or not to add `end_symbol` to the end of the source sequence.
    target_add_start_token : `bool`, (optional, default=`True`)
        Whether or not to add `start_symbol` to the beginning of the target sequence.
    target_add_end_token : `bool`, (optional, default=`True`)
        Whether or not to add `end_symbol` to the end of the target sequence.
    start_symbol : `str`, (optional, default=`START_SYMBOL`)
        The special token to add to the end of the source sequence or the target sequence if
        `source_add_start_token` or `target_add_start_token` respectively.
    end_symbol : `str`, (optional, default=`END_SYMBOL`)
        The special token to add to the end of the source sequence or the target sequence if
        `source_add_end_token` or `target_add_end_token` respectively.
    delimiter : `str`, (optional, default=`"\t"`)
        Set delimiter for tsv/csv file.
    quoting : `int`, (optional, default=`csv.QUOTE_MINIMAL`)
        Quoting to use for csv reader.
    """

    def __init__(
        self,
        source_tokenizer: Tokenizer = None,
        target_tokenizer: Tokenizer = None,
        source_token_indexers: Dict[str, TokenIndexer] = None,
        target_token_indexers: Dict[str, TokenIndexer] = None,
        source_add_start_token: bool = True,
        source_add_end_token: bool = True,
        target_add_start_token: bool = True,
        target_add_end_token: bool = True,
        src_start_symbol: str = START_SYMBOL,
        src_end_symbol: str = END_SYMBOL,
        tgt_start_symbol: str = START_SYMBOL,
        tgt_end_symbol: str = END_SYMBOL,
        delimiter: str = "\t",
        source_max_tokens: Optional[int] = None,
        target_max_tokens: Optional[int] = None,
        quoting: int = csv.QUOTE_MINIMAL,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._source_tokenizer = source_tokenizer or SpacyTokenizer()
        self._target_tokenizer = target_tokenizer or self._source_tokenizer
        self._source_token_indexers = source_token_indexers or {"tokens": SingleIdTokenIndexer()}
        self._target_token_indexers = target_token_indexers or self._source_token_indexers

        self._source_add_start_token = source_add_start_token
        self._source_add_end_token = source_add_end_token
        self._target_add_start_token = target_add_start_token
        self._target_add_end_token = target_add_end_token
        self._src_start_token: Optional[Token] = None
        self._src_end_token: Optional[Token] = None
        self._tgt_start_token: Optional[Token] = None
        self._tgt_end_token: Optional[Token] = None
        if (
            source_add_start_token
            or source_add_end_token
            or target_add_start_token
            or target_add_end_token
        ):
            # Check that the tokenizer correctly appends the start and end tokens to
            # the sequence without splitting them.
            try:
                self._src_start_token, self._src_end_token = self._source_tokenizer.tokenize(
                    src_start_symbol + " " + src_end_symbol
                )
            except ValueError:
                raise ValueError(
                    f"Bad start or end symbol ({'start_symbol', 'end_symbol'}) "
                    f"for tokenizer {self._source_tokenizer}"
                )
            try:
                self._tgt_start_token, self._tgt_end_token = self._target_tokenizer.tokenize(
                    tgt_start_symbol + " " + tgt_end_symbol
                )
            except ValueError:
                raise ValueError(
                    f"Bad start or end symbol ({'start_symbol', 'end_symbol'}) "
                    f"for tokenizer {self._target_tokenizer}"
                )

        self._delimiter = delimiter
        self._source_max_tokens = source_max_tokens
        self._target_max_tokens = target_max_tokens
        self._source_max_exceeded = 0
        self._target_max_exceeded = 0
        self.quoting = quoting

    @overrides
    def _read(self, file_path: str):
        # Reset exceeded counts
        self._source_max_exceeded = 0
        self._target_max_exceeded = 0
        with open(cached_path(file_path), "r") as data_file:
            logger.info("Reading instances from lines in file at: %s", file_path)
            for line_num, row in enumerate(
                csv.reader(data_file, delimiter=self._delimiter, quoting=self.quoting)
            ):
                if len(row) != 2:
                    raise ConfigurationError(
                        "Invalid line format: %s (line number %d)" % (row, line_num + 1)
                    )
                source_sequence, target_sequence = row
                if len(source_sequence) == 0 or len(target_sequence) == 0:
                    continue
                yield self.text_to_instance(source_sequence, target_sequence)
        if self._source_max_tokens and self._source_max_exceeded:
            logger.info(
                "In %d instances, the source token length exceeded the max limit (%d) and were truncated.",
                self._source_max_exceeded,
                self._source_max_tokens,
            )
        if self._target_max_tokens and self._target_max_exceeded:
            logger.info(
                "In %d instances, the target token length exceeded the max limit (%d) and were truncated.",
                self._target_max_exceeded,
                self._target_max_tokens,
            )

    @overrides
    def text_to_instance(
        self, source_string: str, target_string: str = None
    ) -> Instance:  # type: ignore
        tokenized_source = self._source_tokenizer.tokenize(source_string)
        if self._source_max_tokens and len(tokenized_source) > self._source_max_tokens:
            self._source_max_exceeded += 1
            tokenized_source = tokenized_source[: self._source_max_tokens]
        if self._source_add_start_token:
            tokenized_source.insert(0, copy.deepcopy(self._src_start_token))
        if self._source_add_end_token:
            tokenized_source.append(copy.deepcopy(self._src_end_token))
        source_field = TextField(tokenized_source, self._source_token_indexers)
        if target_string is not None:
            tokenized_target = self._target_tokenizer.tokenize(target_string)
            if self._target_max_tokens and len(tokenized_target) > self._target_max_tokens:
                self._target_max_exceeded += 1
                tokenized_target = tokenized_target[: self._target_max_tokens]
            if self._target_add_start_token:
                tokenized_target.insert(0, copy.deepcopy(self._tgt_start_token))
            if self._target_add_end_token:
                tokenized_target.append(copy.deepcopy(self._tgt_end_token))
            target_field = TextField(tokenized_target, self._target_token_indexers)
            return Instance({"source_tokens": source_field, "target_tokens": target_field})
        else:
            return Instance({"source_tokens": source_field})
