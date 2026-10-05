"""Evidence: the self-contained bundle an auditor reads without the repository.

작성자: 최진호
날짜: 2026-10-05

The package turns a verification run into one record that carries the contract
and receipt digests it answers to, the diff and the outputs it was judged by,
and a checksum over those fields so a later edit cannot pass unnoticed.
"""
