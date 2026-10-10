"""
Volume 4.3.1 contract tests: the alias map is tied to BOTH ends of the
pipeline - the Vol 4.2 source contract on the input side, and the real
warehouse models on the output side - so drift on either end fails here.
No database is touched (Django model _meta only).
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pandas as pd
import pytest

from etl.exceptions import MissingColumnsError
from etl.sources import CANONICAL_COLUMNS
from etl.transforms import ALIAS_MAP, TARGET_COLUMNS, AliasMapper, Transform, TransformedBatch
from warehouse.models import Customer, OrderLine, Product

# Which warehouse model owns each target column. Lives in the test, not
# production code: it is documentation of the mapping's destination, and
# the test below keeps it honest against the real models.
TARGET_OWNERS = {
    "invoice_no": OrderLine,
    "stock_code": Product,
    "description": Product,
    "quantity": OrderLine,
    "invoice_date": OrderLine,
    "unit_price": OrderLine,
    "external_customer_id": Customer,
    "country": Customer,
}


class TestAliasMapContract:
    def test_keys_are_exactly_the_vol_4_2_canonical_columns(self):
        assert set(ALIAS_MAP) == set(CANONICAL_COLUMNS)

    def test_no_two_raw_columns_map_to_the_same_target(self):
        assert len(set(ALIAS_MAP.values())) == len(ALIAS_MAP)

    def test_target_columns_follow_canonical_column_order(self):
        assert TARGET_COLUMNS == tuple(ALIAS_MAP[c] for c in CANONICAL_COLUMNS)

    def test_alias_map_is_immutable(self):
        with pytest.raises(TypeError):
            ALIAS_MAP["Invoice"] = "something_else"

    def test_every_target_has_a_declared_owner_and_nothing_extra(self):
        assert set(TARGET_OWNERS) == set(TARGET_COLUMNS)

    @pytest.mark.parametrize("target", TARGET_COLUMNS)
    def test_every_target_is_a_real_field_on_its_warehouse_model(self, target):
        model = TARGET_OWNERS[target]
        field = model._meta.get_field(target)
        assert (
            field.concrete and not field.is_relation
        ), f"{model.__name__}.{target} must be a plain column, not a relation"


class TestTransformedBatch:
    def _frame(self, columns):
        return pd.DataFrame({c: [1] for c in columns})

    def _make(self, df):
        return TransformedBatch(
            data=df,
            source_name="test:source",
            batch_number=3,
            extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_accepts_exactly_the_target_columns(self):
        batch = self._make(self._frame(TARGET_COLUMNS))
        assert len(batch) == 1

    def test_accepts_a_superset_so_later_stages_can_add_columns(self):
        self._make(self._frame([*TARGET_COLUMNS, "is_cancellation"]))

    def test_rejects_missing_target_columns(self):
        cols = [c for c in TARGET_COLUMNS if c != "unit_price"]
        with pytest.raises(MissingColumnsError, match="unit_price"):
            self._make(self._frame(cols))

    def test_is_frozen(self):
        batch = self._make(self._frame(TARGET_COLUMNS))
        with pytest.raises(FrozenInstanceError):
            batch.batch_number = 99


class TestTransformProtocol:
    def test_alias_mapper_satisfies_the_transform_protocol(self):
        assert isinstance(AliasMapper(), Transform)

    def test_an_object_without_transform_does_not(self):
        assert not isinstance(object(), Transform)
