from typing import List
from idp.services.table_processing.models import LogicalBlock, LogicalBlockType


class ContinuationDetector:
    """Detects and stitches split tables/sections across multiple pages."""

    @classmethod
    def merge_continuations(cls, blocks: List[LogicalBlock]) -> List[LogicalBlock]:
        """
        Merges consecutive or identically titled blocks across adjacent pages.
        """
        if not blocks:
            return []

        merged: List[LogicalBlock] = []
        for block in blocks:
            if not merged:
                merged.append(block)
                continue

            prev = merged[-1]
            # Match condition: same block_type, matching non-empty title, across pages
            can_merge = False
            if (
                prev.block_type == block.block_type and
                prev.title and block.title and
                prev.title.strip().lower() == block.title.strip().lower()
            ):
                can_merge = True

            if can_merge:
                # Merge page numbers
                combined_pages = sorted(list(set(prev.page_numbers + block.page_numbers)))
                prev.page_numbers = combined_pages

                # Merge items
                if prev.block_type == LogicalBlockType.KEY_VALUE_TABLE:
                    prev.items.extend(block.items)
                elif prev.block_type == LogicalBlockType.STRUCTURED_TABLE:
                    prev.rows.extend(block.rows)
                    prev.structured_rows.extend(block.structured_rows)
                    prev.summary_rows.extend(block.summary_rows)
            else:
                merged.append(block)

        return merged
