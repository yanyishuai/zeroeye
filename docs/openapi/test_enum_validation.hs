{-# LANGUAGE OverloadedStrings #-}

import Control.Monad (forM_, unless)
import Data.List (isInfixOf)
import Data.Text (Text)
import qualified Data.Text as T
import System.Exit (exitFailure)
import System.FilePath ((</>))

import Tent.OpenAPI.Types (loadOpenApi)
import Tent.OpenAPI.Validate
  ( ValidationError(..)
  , ValidationSeverity(..)
  , validateOpenApi
  )

main :: IO ()
main = do
  let fixtureDir = "docs" </> "openapi" </> "testdata"
  valid <- validateFixture (fixtureDir </> "enum-valid.yaml")
  duplicate <- validateFixture (fixtureDir </> "enum-duplicate.yaml")
  empty <- validateFixture (fixtureDir </> "enum-empty.yaml")
  unsupported <- validateFixture (fixtureDir </> "enum-unsupported.yaml")

  assertNoErrors "valid enum fixture" valid
  assertHasError "duplicate enum fixture" "Duplicate enum value" duplicate
  assertHasError "empty enum fixture" "Enum arrays must contain at least one value" empty
  assertHasError "unsupported enum fixture" "Unsupported enum value type: object" unsupported

validateFixture :: FilePath -> IO [ValidationError]
validateFixture path = do
  parsed <- loadOpenApi path
  case parsed of
    Left err -> do
      putStrLn $ "failed to parse fixture " ++ path ++ ": " ++ show err
      exitFailure
    Right spec -> validateOpenApi spec

assertNoErrors :: String -> [ValidationError] -> IO ()
assertNoErrors label errors =
  unless (null realErrors) $ do
    putStrLn $ label ++ " unexpectedly produced enum validation errors:"
    forM_ realErrors print
    exitFailure
  where
    realErrors = filter ((== Error) . veSeverity) errors

assertHasError :: String -> String -> [ValidationError] -> IO ()
assertHasError label expected errors =
  unless (any (messageContains expected) errors) $ do
    putStrLn $ label ++ " did not produce expected error: " ++ expected
    forM_ errors print
    exitFailure

messageContains :: String -> ValidationError -> Bool
messageContains needle err =
  needle `isInfixOf` T.unpack (veMessage err)
