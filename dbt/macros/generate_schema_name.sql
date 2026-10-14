{#
  By default dbt names custom schemas "<target_schema>_<custom>", e.g.
  analytics_silver. For a medallion warehouse we want the plain layer names:
  silver, gold, reference.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
