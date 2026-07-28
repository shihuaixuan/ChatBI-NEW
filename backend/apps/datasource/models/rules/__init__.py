"""Datasource 领域规则。"""
from apps.datasource.models.rules.sql_query import ReadOnlySQLRule, SQLRuleResult

__all__ = ["ReadOnlySQLRule", "SQLRuleResult"]
