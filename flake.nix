{
  description = "KonomiTV - Flake with separate client/server builds";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };

  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./server; };
        overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
        pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
          python = pkgs.python311;
          # OpenCV の macOS wheel は macOS 13 以降を対象にするため、wheel 互換性判定に現在の SDK を渡す
          stdenv = if pkgs.stdenv.isDarwin then pkgs.stdenv.override {
            targetPlatform = pkgs.stdenv.targetPlatform // {
              darwinSdkVersion = "15.1";
            };
          } else pkgs.stdenv;
        }).overrideScope (pkgs.lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
        ]);
        konomitv-client = import ./client/package.nix { inherit pkgs; };
        konomitv-server = pythonSet.mkVirtualEnv "konomitv-server" workspace.deps.default;

      in
      {
        packages = {
          inherit konomitv-client konomitv-server;
          default = konomitv-server;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.nodejs
            pkgs.python311
            pkgs.uv
          ];
        };
      }
    );
}
