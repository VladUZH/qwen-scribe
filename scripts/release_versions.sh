#!/bin/bash
# Shared version predicates for app builds, archives, and release CI.
# This file is sourced; it deliberately does not change the caller's shell
# options or environment.

qs_valid_bundle_version() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

qs_valid_release_version() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]]
}
