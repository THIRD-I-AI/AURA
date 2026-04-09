import os
import uuid
import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any
import json

import aiofiles
import numpy as np
import pandas as pd
from fastapi import UploadFile, HTTPException


class FileService:
    """Service for handling file uploads, storage, and processing"""

    def __init__(self):
        self.base_path = Path(__file__).parent.parent / "data"
        self.uploads_path = self.base_path / "uploads"
        self.processed_path = self.base_path / "processed"
        self.temp_path = self.base_path / "temp"

        # Ensure directories exist
        self.uploads_path.mkdir(parents=True, exist_ok=True)
        self.processed_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)

        # Supported file types
        self.supported_types = {
            'text/csv': ['.csv'],
            'application/json': ['.json'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            'application/vnd.ms-excel': ['.xls'],
            'text/plain': ['.txt'],
            'application/octet-stream': ['.parquet'],  # Parquet files
            'application/x-parquet': ['.parquet'],     # Alternative MIME type
        }

        # Maximum file size (25MB - increased for Parquet files)
        self.max_file_size = 25 * 1024 * 1024

    def generate_file_id(self) -> str:
        """Generate unique file ID"""
        return str(uuid.uuid4())

    def calculate_file_hash(self, content: bytes) -> str:
        """Calculate SHA256 hash of file content"""
        return hashlib.sha256(content).hexdigest()

    def validate_file(self, file: UploadFile) -> Dict[str, Any]:
        """Validate uploaded file"""
        file_ext = Path(file.filename).suffix.lower()
        content_type = file.content_type

        supported = False
        for mime_type, extensions in self.supported_types.items():
            if content_type == mime_type or file_ext in extensions:
                supported = True
                break

        if not supported:
            supported_extensions: List[str] = []
            for extensions in self.supported_types.values():
                supported_extensions.extend(extensions)
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type. Supported formats: {', '.join(supported_extensions)}"
            )

        return {
            'filename': file.filename,
            'content_type': content_type,
            'file_extension': file_ext,
            'file_size': file.size
        }

    async def save_file(self, file: UploadFile) -> Dict[str, Any]:
        """Save uploaded file to storage"""
        file_info = self.validate_file(file)

        file_id = self.generate_file_id()
        original_filename = file_info['filename']
        file_extension = file_info['file_extension']

        stored_filename = f"{file_id}{file_extension}"
        file_path = self.uploads_path / stored_filename

        content = await file.read()
        file_hash = self.calculate_file_hash(content)

        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)

        metadata = {
            'file_id': file_id,
            'original_filename': original_filename,
            'stored_filename': stored_filename,
            'file_path': str(file_path),
            'content_type': file_info['content_type'],
            'file_extension': file_extension,
            'file_size': len(content),
            'file_hash': file_hash,
            'upload_time': datetime.now(timezone.utc).isoformat(),
            'status': 'uploaded'
        }

        return metadata

    async def process_file(self, file_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Process uploaded file and extract data"""
        file_path = Path(file_metadata['file_path'])
        file_extension = file_metadata['file_extension']

        try:
            processed_data: Any = None
            rows_count = 0
            columns_count = 0
            profile: Dict[str, Any] = {}

            if file_extension == '.csv':
                df = pd.read_csv(file_path)
                first_row = df.iloc[0] if len(df) > 0 else None
                if first_row is not None:
                    all_numeric = all(
                        isinstance(val, (int, float)) or
                        str(val).replace('.', '').replace('-', '').replace(':', '').replace(' ', '').replace(',', '').isdigit()
                        for val in first_row
                    )
                    if all_numeric:
                        df = pd.read_csv(file_path, header=None)
                df.columns = [f'column_{i}' for i in range(len(df.columns))]
                processed_data = df.to_dict('records')
                rows_count = len(df)
                columns_count = len(df.columns)
                profile = self._profile_dataframe(df)

            elif file_extension in ['.xlsx', '.xls']:
                df = pd.read_excel(file_path)
                first_row = df.iloc[0] if len(df) > 0 else None
                if first_row is not None:
                    all_numeric = all(
                        isinstance(val, (int, float)) or
                        str(val).replace('.', '').replace('-', '').replace(':', '').replace(' ', '').replace(',', '').isdigit()
                        for val in first_row
                    )
                    if all_numeric:
                        df = pd.read_excel(file_path, header=None)
                df.columns = [f'column_{i}' for i in range(len(df.columns))]
                processed_data = df.to_dict('records')
                rows_count = len(df)
                columns_count = len(df.columns)
                profile = self._profile_dataframe(df)

            elif file_extension == '.parquet':
                import pyarrow.parquet as pq
                df = pd.read_parquet(file_path)
                df.columns = [f'column_{i}' if isinstance(col, int) or str(col).isdigit() else str(col) for i, col in enumerate(df.columns)]
                processed_data = df.to_dict('records')
                rows_count = len(df)
                columns_count = len(df.columns)
                profile = self._profile_dataframe(df)

                parquet_file = pq.ParquetFile(file_path)
                parquet_metadata = {
                    'num_row_groups': parquet_file.num_row_groups,
                    'schema': str(parquet_file.schema),
                    'compression': str(parquet_file.metadata.row_group(0).column(0).compression) if parquet_file.num_row_groups > 0 else 'unknown'
                }
                file_metadata['parquet_info'] = parquet_metadata

            elif file_extension == '.json':
                with open(file_path, 'r', encoding='utf-8') as f:
                    processed_data = json.load(f)

                if isinstance(processed_data, list):
                    rows_count = len(processed_data)
                    columns_count = len(processed_data[0].keys()) if processed_data else 0
                    try:
                        df = pd.DataFrame(processed_data)
                        profile = self._profile_dataframe(df)
                    except Exception:
                        profile = {}
                elif isinstance(processed_data, dict):
                    rows_count = 1
                    columns_count = len(processed_data.keys()) if isinstance(processed_data, dict) else 0
                    try:
                        df = pd.DataFrame([processed_data])
                        profile = self._profile_dataframe(df)
                    except Exception:
                        profile = {}

            elif file_extension == '.txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                try:
                    processed_data = json.loads(content)
                    if isinstance(processed_data, list):
                        rows_count = len(processed_data)
                        columns_count = len(processed_data[0].keys()) if processed_data else 0
                        df = pd.DataFrame(processed_data)
                        profile = self._profile_dataframe(df)
                    elif isinstance(processed_data, dict):
                        rows_count = 1
                        columns_count = len(processed_data.keys()) if processed_data else 0
                        df = pd.DataFrame([processed_data])
                        profile = self._profile_dataframe(df)
                except json.JSONDecodeError:
                    lines = content.strip().split('\n')
                    if len(lines) > 1:
                        headers = [h.strip() for h in lines[0].split(',')]
                        processed_data = []
                        for line in lines[1:]:
                            values = [v.strip() for v in line.split(',')]
                            row = {}
                            for i, header in enumerate(headers):
                                if i < len(values):
                                    value = values[i]
                                    try:
                                        row[header] = float(value) if '.' in value else int(value)
                                    except ValueError:
                                        row[header] = value
                                else:
                                    row[header] = None
                            processed_data.append(row)
                        rows_count = len(processed_data)
                        columns_count = len(headers)
                        try:
                            df = pd.DataFrame(processed_data)
                            profile = self._profile_dataframe(df)
                        except Exception:
                            profile = {}

            # Save processed data
            processed_filename = f"{file_metadata['file_id']}_processed.json"
            processed_path = self.processed_path / processed_filename

            with open(processed_path, 'w', encoding='utf-8') as f:
                json.dump(processed_data, f, indent=2, default=str)

            file_metadata.update({
                'status': 'processed',
                'processed_path': str(processed_path),
                'processed_filename': processed_filename,
                'rows_count': rows_count,
                'columns_count': columns_count,
                'processed_time': datetime.now(timezone.utc).isoformat(),
                'preview_data': processed_data[:5] if isinstance(processed_data, list) else processed_data,
                'profile': profile
            })

            return file_metadata

        except Exception as e:
            file_metadata['status'] = 'error'
            file_metadata['error'] = str(e)
            file_metadata['error_time'] = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")

    def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get file information by ID"""
        # This would typically query the database
        # For now, we'll implement a simple file-based lookup
        pass

    def list_files(self) -> List[Dict[str, Any]]:
        """List all uploaded files"""
        files = []
        for file_path in self.uploads_path.glob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    'filename': file_path.name,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        return files

    def delete_file(self, file_id: str) -> bool:
        """Delete file by ID"""
        try:
            for file_path in self.uploads_path.glob(f"{file_id}.*"):
                file_path.unlink()

            for file_path in self.processed_path.glob(f"{file_id}_processed.*"):
                file_path.unlink()

            return True
        except Exception:
            return False

    def _profile_dataframe(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Create lightweight column-level profile for a dataframe."""
        profile: Dict[str, Any] = {
            'rows': int(df.shape[0]),
            'columns': int(df.shape[1]),
            'columns_profile': {}
        }

        for col in df.columns:
            series = df[col]
            col_profile: Dict[str, Any] = {}

            non_null = series.notna().sum()
            nulls = series.isna().sum()
            distinct = series.nunique(dropna=True)

            col_profile['non_null'] = int(non_null)
            col_profile['nulls'] = int(nulls)
            col_profile['distinct'] = int(distinct)

            dtype = self._infer_dtype(series)
            col_profile['inferred_type'] = dtype

            samples = series.dropna().astype(str).unique()[:3].tolist()
            col_profile['samples'] = samples

            if dtype == 'numeric':
                col_profile['min'] = self._to_serializable(series.min())
                col_profile['max'] = self._to_serializable(series.max())
                col_profile['mean'] = self._to_serializable(series.mean())
            elif dtype == 'datetime':
                col_profile['min'] = self._to_serializable(series.min())
                col_profile['max'] = self._to_serializable(series.max())

            value_counts = series.value_counts(dropna=True).head(3)
            col_profile['top_values'] = {str(k): int(v) for k, v in value_counts.items()}

            profile['columns_profile'][str(col)] = col_profile

        return profile

    def _infer_dtype(self, series: pd.Series) -> str:
        """Infer a simple dtype label for profiling purposes."""
        if pd.api.types.is_numeric_dtype(series):
            return 'numeric'
        if pd.api.types.is_datetime64_any_dtype(series):
            return 'datetime'
        return 'categorical'

    def _to_serializable(self, value: Any) -> Any:
        """Convert numpy/pandas scalars to plain Python types for JSON serialization."""
        if isinstance(value, (np.generic,)):
            return value.item()
        if isinstance(value, (pd.Timestamp,)):
            return value.isoformat()
        return value


# Global file service instance
file_service = FileService()
