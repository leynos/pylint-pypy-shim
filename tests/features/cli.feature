Feature: Pylint PyPy shim CLI
  Scenario: Linting a trivial module through the CLI succeeds
    Given a trivial Python module
    When I run the pylint-pypy shim CLI
    Then the CLI exits successfully

  Scenario: Linting a module with a violation through the CLI fails
    Given a Python module with a Pylint violation
    When I run the pylint-pypy shim CLI
    Then the CLI exits with a non-zero status
    And the CLI output contains "missing-module-docstring"
