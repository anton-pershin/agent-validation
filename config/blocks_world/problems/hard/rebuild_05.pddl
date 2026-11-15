(define (problem rebuild-05)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear d) (hand-empty) (on a b) (on b c) (on c e) (on d a) (on-table e))
  (:goal (and (on a b) (on-table b) (on-table c) (on-table d) (on-table e)))
)
