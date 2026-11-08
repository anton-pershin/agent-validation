(define (problem rebuild-11)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear d) (hand-empty) (on a c) (on b a) (on c e) (on d b) (on-table e))
  (:goal (and (on b c) (on-table a) (on-table c) (on-table d) (on-table e)))
)
