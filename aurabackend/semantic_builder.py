"""
Semantic Model Auto-Builder
Generates semantic models from dataset profiles with intelligent field classification.
"""

from typing import Dict, List, Any, Optional
import uuid
from datetime import datetime


class SemanticModelBuilder:
    """Auto-generates semantic models from dataset profiles."""

    def __init__(self):
        self.dimension_keywords = {
            'id', 'name', 'category', 'type', 'status', 'country', 'region',
            'city', 'state', 'code', 'key', 'date', 'time', 'period', 'month',
            'year', 'quarter', 'week', 'day', 'hour', 'minute', 'second',
            'product', 'customer', 'user', 'account', 'order', 'transaction',
            'employee', 'department', 'location', 'source', 'target', 'gender',
            'age_group', 'segment', 'channel', 'brand', 'group', 'class'
        }

        self.measure_keywords = {
            'count', 'total', 'sum', 'amount', 'value', 'price', 'cost', 'revenue',
            'profit', 'margin', 'rate', 'percentage', 'percent', 'avg', 'average',
            'mean', 'min', 'max', 'quantity', 'sales', 'units', 'volume', 'weight',
            'distance', 'time', 'duration', 'minutes', 'hours', 'days', 'seconds',
            'score', 'rating', 'rank', 'index', 'balance', 'deposit', 'withdrawal',
            'fee', 'commission', 'tax', 'discount', 'subtotal', 'grand_total'
        }

        self.aggregation_defaults = {
            'numeric': 'sum',
            'categorical': None,
            'datetime': 'min',
        }

    def generate_model_from_profile(
        self,
        file_id: str,
        dataset_name: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Auto-generate semantic model from dataset profile.
        
        Args:
            file_id: Unique file identifier
            dataset_name: Human-readable dataset name
            profile: Dataset profile from file_service with column statistics
            
        Returns:
            Semantic model payload with auto-classified fields
        """
        if not profile or 'columns_profile' not in profile:
            raise ValueError("Profile missing columns_profile")

        columns_profile = profile['columns_profile']
        fields: List[Dict[str, Any]] = []

        for col_name, col_stats in columns_profile.items():
            field = self._classify_field(col_name, col_stats)
            fields.append(field)

        model_payload = {
            'name': dataset_name or f'model_{file_id[:8]}',
            'description': self._generate_description(dataset_name, profile),
            'source': {
                'type': 'file',
                'file_id': file_id,
                'dataset_name': dataset_name,
                'rows': profile.get('rows', 0),
                'columns': profile.get('columns', 0),
            },
            'tags': self._infer_tags(dataset_name, fields),
            'fields': fields,
        }

        return model_payload

    def _classify_field(self, col_name: str, col_stats: Dict[str, Any]) -> Dict[str, Any]:
        """Classify a field as dimension, measure, or metric."""
        inferred_type = col_stats.get('inferred_type', 'categorical')
        col_name_lower = col_name.lower().replace('_', ' ')

        # Check for keyword matches
        is_dimension = any(kw in col_name_lower for kw in self.dimension_keywords)
        is_measure = any(kw in col_name_lower for kw in self.measure_keywords)

        # Default rules: numeric → measure, datetime → dimension, categorical → dimension
        if is_measure or (inferred_type == 'numeric' and not is_dimension):
            field_type = 'measure'
            aggregation = self.aggregation_defaults.get(inferred_type, 'sum')
        else:
            field_type = 'dimension'
            aggregation = None

        description = self._generate_field_description(col_name, col_stats, field_type)

        return {
            'id': str(uuid.uuid4()),
            'name': col_name,
            'field_type': field_type,
            'data_type': inferred_type,
            'description': description,
            'aggregation': aggregation,
            'metadata': {
                'non_null': col_stats.get('non_null', 0),
                'nulls': col_stats.get('nulls', 0),
                'distinct': col_stats.get('distinct', 0),
                'top_values': col_stats.get('top_values', {}),
                'samples': col_stats.get('samples', []),
            },
        }

    def _generate_field_description(
        self,
        col_name: str,
        col_stats: Dict[str, Any],
        field_type: str,
    ) -> str:
        """Generate a human-readable field description from stats."""
        inferred_type = col_stats.get('inferred_type', 'unknown')
        distinct = col_stats.get('distinct', 0)
        nulls = col_stats.get('nulls', 0)

        parts = []
        parts.append(f"{inferred_type.capitalize()} field")

        if field_type == 'measure':
            parts.append(f"(aggregated by sum by default)")
        elif distinct > 0:
            parts.append(f"with {distinct} distinct values")

        if nulls > 0:
            parts.append(f", {nulls} nulls")

        return ', '.join(parts)

    def _generate_description(self, dataset_name: str, profile: Dict[str, Any]) -> str:
        """Generate model-level description from profile."""
        rows = profile.get('rows', 0)
        cols = profile.get('columns', 0)
        return f"Semantic model for {dataset_name or 'dataset'} ({rows} rows, {cols} columns)"

    def _infer_tags(self, dataset_name: str, fields: List[Dict[str, Any]]) -> List[str]:
        """Infer tags based on dataset name and field types."""
        tags = []

        # Dataset domain tags
        name_lower = (dataset_name or '').lower()
        if any(w in name_lower for w in ['sales', 'order', 'customer', 'transaction']):
            tags.append('sales')
        if any(w in name_lower for w in ['product', 'inventory', 'catalog']):
            tags.append('products')
        if any(w in name_lower for w in ['employee', 'hr', 'staff', 'department']):
            tags.append('hr')
        if any(w in name_lower for w in ['finance', 'budget', 'accounting']):
            tags.append('finance')
        if any(w in name_lower for w in ['marketing', 'campaign', 'lead']):
            tags.append('marketing')

        # Field composition tags
        measure_count = sum(1 for f in fields if f['field_type'] == 'measure')
        dimension_count = sum(1 for f in fields if f['field_type'] == 'dimension')

        if measure_count > 0:
            tags.append('aggregatable')
        if dimension_count > 0:
            tags.append('dimensional')

        if not tags:
            tags.append('general')

        return list(set(tags))  # Remove duplicates

    def update_model_from_profile(
        self,
        existing_model: Dict[str, Any],
        updated_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update an existing model with new profile stats (e.g., after data refresh).
        
        Keeps existing field definitions but updates metadata from profile.
        """
        if 'columns_profile' not in updated_profile:
            return existing_model

        columns_profile = updated_profile['columns_profile']
        updated_fields = []

        for field in existing_model.get('fields', []):
            col_name = field['name']
            if col_name in columns_profile:
                col_stats = columns_profile[col_name]
                field['metadata'] = {
                    'non_null': col_stats.get('non_null', 0),
                    'nulls': col_stats.get('nulls', 0),
                    'distinct': col_stats.get('distinct', 0),
                    'top_values': col_stats.get('top_values', {}),
                    'samples': col_stats.get('samples', []),
                }
            updated_fields.append(field)

        existing_model['fields'] = updated_fields
        existing_model['source']['rows'] = updated_profile.get('rows', 0)
        existing_model['source']['columns'] = updated_profile.get('columns', 0)

        return existing_model


# Global builder instance
semantic_builder = SemanticModelBuilder()
