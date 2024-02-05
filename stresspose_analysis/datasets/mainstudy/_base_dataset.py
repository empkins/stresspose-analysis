from itertools import product
from typing import Optional, Sequence, Tuple

import pandas as pd
from biopsykit.io import (
    load_atimelogger_file,
    load_codebook,
    load_long_format_csv,
    load_questionnaire_data,
)
from biopsykit.utils.file_handling import get_subject_dirs
from tpcp import Dataset

from empkins_io.datasets.d03.macro_ap01.helper import _build_data_path
from empkins_io.utils._types import path_t
from empkins_io.utils.exceptions import TimelogNotFoundException


class MainStudyBaseDataset(Dataset):
    base_path: path_t
    use_cache: bool
    _sample_times_saliva: Tuple[int] = (-40, -1, 15, 25, 35, 45, 60, 75)
    _sample_times_bloodspot: Tuple[int] = (-40, 60)

    CONDITIONS = ["ftsst", "tsst"]

    SUBSETS_WITHOUT_MOCAP = (
        ("VP_03", "tsst"),  # no math phase in tsst (aborted)
        ("VP_31", "ftsst"),  # probably wrong sensor placement or calibration
    )

    def __init__(
        self,
        base_path: path_t,
        groupby_cols: Optional[Sequence[str]] = None,
        subset_index: Optional[Sequence[str]] = None,
        *,
        exclude_complete_subjects_if_error: bool = True,
        exclude_without_mocap: bool = True,
        use_cache: bool = True,
    ):
        # ensure pathlib
        self.base_path = base_path
        self.exclude_complete_subjects_if_error = exclude_complete_subjects_if_error
        self.exclude_without_mocap = exclude_without_mocap

        self.data_to_exclude = self._find_data_to_exclude(exclude_complete_subjects_if_error)
        self.use_cache = use_cache

        super().__init__(groupby_cols=groupby_cols, subset_index=subset_index)

    def create_index(self):
        subject_ids = [
            subject_dir.name for subject_dir in get_subject_dirs(self.base_path.joinpath("data_per_subject"), "VP_*")
        ]
        index_cols = ["subject", "condition"]
        index = list(product(subject_ids, self.CONDITIONS))

        index = pd.DataFrame(index, columns=index_cols)
        index = index.set_index(index_cols)
        index = index.drop(index=self.data_to_exclude).reset_index()

        return index

    def _find_data_to_exclude(self, exclude_complete_subjects_if_error: bool):
        data_to_exclude = []
        if self.exclude_without_mocap:
            data_to_exclude += self.SUBSETS_WITHOUT_MOCAP

        if exclude_complete_subjects_if_error:
            data_to_exclude = [x[0] for x in data_to_exclude]

        return data_to_exclude

    @property
    def subject(self) -> str:
        if not self.is_single("subject"):
            raise ValueError("Subject data can only be accessed for a single participant!")
        return self.index["subject"][0]

    @property
    def condition(self) -> str:
        if not self.is_single("condition"):
            raise ValueError("Condition data can only be accessed for a single condition!")
        return self.index["condition"][0]

    @property
    def sampling_rate(self) -> float:
        """Sampling rate of the MoCap system."""
        return 60

    @property
    def sample_times_saliva(self) -> Sequence[int]:
        return self._sample_times_saliva

    @property
    def sample_times_bloodspot(self) -> Sequence[int]:
        return self._sample_times_bloodspot

    @property
    def timelog_test(self) -> pd.DataFrame:
        return self._load_time_log("test")

    def _load_time_log(self, timelog_type: str):
        subject_id = self.index["subject"][0]
        condition = self.index["condition"][0]
        data_path = _build_data_path(self.base_path.joinpath("data_per_subject"), subject_id, condition)
        file_path = data_path.joinpath(f"timelog/cleaned/{subject_id}_{condition}_timelog_{timelog_type}.csv")
        if not file_path.exists():
            raise TimelogNotFoundException(
                f"No time log data was found for {timelog_type} in the {condition} condition of {subject_id}!"
            )
        timelog = load_atimelogger_file(file_path, timezone="Europe/Berlin")
        # convert all column names of the multi-level column index to lower case
        timelog.columns = timelog.columns.set_levels([level.str.lower() for level in timelog.columns.levels])

        return timelog

    @property
    def timelog_total(self) -> pd.DataFrame:
        return self.timelog_test.sort_values(by="time", axis=1)

    @property
    def questionnaire(self) -> pd.DataFrame:
        if self.is_single(["condition"]):
            raise ValueError("Questionnaire data can not be accessed for a single condition!")
        data = load_questionnaire_data(self.base_path.joinpath("questionnaires/merged_total/questionnaire_data.xlsx"))
        subject_ids = self.index["subject"].unique()
        return data.loc[subject_ids]

    @property
    def gender(self) -> pd.Series:
        return self.questionnaire["Gender"]

    @property
    def questionnaire_scores(self) -> pd.DataFrame:
        data_path = self.base_path.joinpath("questionnaires/processed/questionnaire_data_processed.csv")
        if not data_path.exists():
            raise ValueError(
                "Processed questionnaire data not available! "
                "Please run the 'questionnaires/Questionnaire_Processing.ipynb' notebook first!"
            )
        data = load_long_format_csv(data_path)
        subject_ids = self.index["subject"].unique()
        conditions = self.index["condition"].unique()
        return data.reindex(subject_ids, level="subject").reindex(conditions, level="condition")

    @property
    def pasa(self) -> pd.DataFrame:
        return self._extract_questionnaire_score("PASA")

    @property
    def stadi_state(self) -> pd.DataFrame:
        return self._extract_questionnaire_score("STADI_State")

    @property
    def panas(self) -> pd.DataFrame:
        return self._extract_questionnaire_score("PANAS")

    @property
    def panas_diff(self) -> pd.DataFrame:
        panas_data = self.panas
        panas_data = panas_data.drop("Total", level="subscale")
        panas_data = panas_data.reindex(["ftsst", "tsst"], level="condition").reindex(["pre", "post"], level="time")
        panas_data = panas_data.unstack("time").diff(axis=1).stack().droplevel(-1)
        return panas_data.reorder_levels(["subject", "condition", "subscale"])

    @property
    def stadi_state_diff(self) -> pd.DataFrame:
        stadi_data = self.stadi_state
        stadi_data = stadi_data.reindex(["pre", "post"], level="time")
        stadi_data = stadi_data.unstack("time").diff(axis=1).stack().droplevel(-1)
        return stadi_data.reorder_levels(["subject", "condition", "subscale"])

    @property
    def codebook(self) -> pd.DataFrame:
        return load_codebook(self.base_path.joinpath("questionnaires/codebook.csv"))

    @property
    def condition_order(self) -> pd.DataFrame:
        data = pd.read_csv(self.base_path.joinpath("extras/condition_order.csv"))
        data = data.set_index("subject")[["condition_order"]]
        subject_ids = self.index["subject"].unique()
        return data.loc[subject_ids]

    @property
    def day_condition_map(self) -> pd.DataFrame:
        data = pd.read_csv(self.base_path.joinpath("extras/condition_order.csv"))
        data = data.set_index("subject")[["T1", "T2"]].stack()
        data.index = data.index.set_names("day", level=-1)
        data = pd.DataFrame(data, columns=["condition"])
        return data

    @property
    def cort_non_responder(self) -> pd.Series:
        non_responder = self.cortisol_features.xs("tsst", level="condition")
        non_responder = non_responder.xs("max_inc", level="saliva_feature") <= 1.5
        non_responder.columns = ["non_responder"]
        subject_ids = self.index["subject"].unique()
        return non_responder.loc[subject_ids]

    @property
    def cortisol(self) -> pd.DataFrame:
        return self._load_saliva_data("cortisol")

    @property
    def cortisol_features(self) -> pd.DataFrame:
        return self._load_saliva_features("cortisol")

    @property
    def amylase(self) -> pd.DataFrame:
        return self._load_saliva_data("amylase")

    @property
    def amylase_features(self) -> pd.DataFrame:
        return self._load_saliva_features("amylase")

    @property
    def progesterone(self) -> pd.DataFrame:
        return self._load_estradiol_progesterone()[["progesterone"]]

    @property
    def estradiol(self) -> pd.DataFrame:
        return self._load_estradiol_progesterone()[["estradiol"]]

    @property
    def blood_spots(self) -> pd.DataFrame:
        data_path = self.base_path.joinpath("bloodspots/processed/crp_samples.csv")
        if not data_path.exists():
            raise ValueError(
                "Processed bloodspot data not available! "
                "Please run the 'biomarker/Bloodspot_Processing.ipynb' notebook first!"
            )
        data = load_long_format_csv(data_path)
        subject_ids = self.index["subject"].unique()
        conditions = self.index["condition"].unique()
        return data.reindex(subject_ids, level="subject").reindex(conditions, level="condition")

    def add_cortisol_index(self, cort_data: pd.DataFrame) -> pd.DataFrame:
        index_levels = list(cort_data.index.names)
        new_index_levels = ["condition_order", "non_responder"]
        cort_data = cort_data.join(self.condition_order).join(self.cort_non_responder)
        cort_data = cort_data.set_index(new_index_levels, append=True)
        cort_data = cort_data.reorder_levels(index_levels[:-1] + new_index_levels + [index_levels[-1]])

        return cort_data

    def _load_estradiol_progesterone(self):
        data_path = self.base_path.joinpath("saliva/processed/progesterone_estradiol_samples.csv")
        if not data_path.exists():
            raise ValueError(
                "Processed saliva data not available! "
                "Please run the 'biomarker/Saliva_Processing.ipynb' notebook first!"
            )
        data = pd.read_csv(data_path)
        data = data.set_index("subject")
        subject_ids = self.index["subject"].unique()
        return data.reindex(subject_ids).dropna()

    def _extract_questionnaire_score(self, score_type: str):
        data = self.questionnaire_scores
        return data["data"].unstack("type")[[score_type]].dropna()

    def _load_saliva_data(self, saliva_type: str) -> pd.DataFrame:
        data_path = self.base_path.joinpath(f"saliva/processed/{saliva_type}_samples.csv")
        if not data_path.exists():
            raise ValueError(
                "Processed saliva data not available! "
                "Please run the 'biomarker/Saliva_Processing.ipynb' notebook first!"
            )
        data = load_long_format_csv(data_path)

        subject_ids = self.index["subject"].unique()
        conditions = self.index["condition"].unique()
        return data.reindex(subject_ids, level="subject").reindex(conditions, level="condition")

    def _load_saliva_features(self, saliva_type: str) -> pd.DataFrame:
        data_path = self.base_path.joinpath(f"saliva/processed/{saliva_type}_features.csv")
        data = load_long_format_csv(data_path)
        subject_ids = self.index["subject"].unique()
        conditions = self.index["condition"].unique()
        return data.reindex(subject_ids, level="subject").reindex(conditions, level="condition")
