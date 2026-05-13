Feature: Pylint PyPy shim CLI
  Scenario: Linting a trivial module through the CLI succeeds
    Given a trivial Python module
    When I run the pylint-pypy shim CLI
    Then the CLI exits successfully
